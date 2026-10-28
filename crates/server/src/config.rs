//! Strict startup configuration; all workspace roots are fixed before serving.

use agentfence_core::Policy;
use serde::Deserialize;
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

/// A safe startup error, distinct from public per-request errors.
#[derive(Debug, thiserror::Error)]
pub enum StartupError {
    /// Invalid configuration or credential setup.
    #[error("{0}")]
    Invalid(&'static str),
    /// An operating-system operation failed during startup.
    #[error("startup I/O failed: {0}")]
    Io(#[from] std::io::Error),
    /// Configuration JSON does not match the schema.
    #[error("invalid policy JSON: {0}")]
    Json(#[from] serde_json::Error),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawConfig {
    workspaces: BTreeMap<String, RawWorkspace>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawWorkspace {
    root: PathBuf,
    allowed_paths: Vec<String>,
    #[serde(default = "default_limit")]
    max_content_bytes: usize,
}

fn default_limit() -> usize {
    65_536
}

/// An immutable configured workspace. No client can replace its root.
#[derive(Debug, Clone)]
pub struct Workspace {
    /// Canonical administrator-selected directory, never returned by HTTP APIs.
    pub(crate) root: PathBuf,
    /// Validated pure path/content policy.
    pub(crate) policy: Policy,
    pub(crate) gate: Arc<Mutex<()>>,
    pub(crate) snapshot: Option<Arc<crate::workspace::Snapshot>>,
}

/// Validated workspace registry, loaded once without hot updates.
#[derive(Debug, Clone)]
pub struct Configuration {
    pub(crate) workspaces: BTreeMap<String, Workspace>,
}

/// Validate an identifier used in route/query parameters (not a path).
#[must_use]
pub fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 128
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

impl Configuration {
    /// Start a v2 registry with no preauthorized live-project roots.
    ///
    /// Only a separately authenticated administrator can import a workspace.
    #[must_use]
    pub fn empty() -> Self {
        Self {
            workspaces: BTreeMap::new(),
        }
    }

    /// Load JSON; relative roots are relative to the policy file's directory.
    pub fn load(path: &Path) -> Result<Self, StartupError> {
        let canonical = path.canonicalize()?;
        let parent = canonical
            .parent()
            .ok_or(StartupError::Invalid("policy must have a parent directory"))?;
        Self::from_json(&fs::read_to_string(&canonical)?, parent)
    }

    /// Validate JSON against a fixed base directory, also useful for fixtures.
    ///
    /// Roots must already exist. Allowed files may be absent so authorized
    /// requests can report FILE_NOT_FOUND without changing configuration.
    pub fn from_json(json: &str, base: &Path) -> Result<Self, StartupError> {
        let raw: RawConfig = serde_json::from_str(json)?;
        if raw.workspaces.is_empty() {
            return Err(StartupError::Invalid("at least one workspace is required"));
        }
        let mut workspaces = BTreeMap::new();
        for (id, value) in raw.workspaces {
            if !valid_id(&id) {
                return Err(StartupError::Invalid("invalid workspace_id"));
            }
            let root = base.join(value.root).canonicalize()?;
            if !root.is_dir() {
                return Err(StartupError::Invalid(
                    "workspace root must be an existing directory",
                ));
            }
            let policy = Policy::new(value.allowed_paths, value.max_content_bytes)
                .map_err(|_| StartupError::Invalid("invalid allowlist or content limit"))?;
            workspaces.insert(
                id,
                Workspace {
                    root,
                    policy,
                    gate: Arc::new(Mutex::new(())),
                    snapshot: None,
                },
            );
        }
        Ok(Self { workspaces })
    }

    /// Resolve a new/existing audit path and reject overlap before creating it.
    pub(crate) fn audit_path(&self, path: &Path) -> Result<PathBuf, StartupError> {
        let input = if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir()?.join(path)
        };
        // Normalize dot segments before testing containment, including segments
        // after a not-yet-existing parent. Otherwise new/../workspace/audit could
        // appear outside the root lexically but create a log inside it.
        let mut absolute = PathBuf::new();
        for component in input.components() {
            match component {
                std::path::Component::CurDir => {}
                std::path::Component::ParentDir => {
                    if !absolute.pop() {
                        return Err(StartupError::Invalid("invalid audit path"));
                    }
                }
                _ => absolute.push(component.as_os_str()),
            }
        }
        if let Ok(meta) = fs::symlink_metadata(&absolute)
            && (meta.file_type().is_symlink() || !meta.is_file())
        {
            return Err(StartupError::Invalid(
                "audit must be a regular file outside all workspaces",
            ));
        }
        let mut ancestor = absolute.as_path();
        let mut suffix = Vec::new();
        while !ancestor.exists() {
            suffix.push(
                ancestor
                    .file_name()
                    .ok_or(StartupError::Invalid("invalid audit path"))?
                    .to_owned(),
            );
            ancestor = ancestor
                .parent()
                .ok_or(StartupError::Invalid("invalid audit parent"))?;
        }
        let mut resolved = ancestor.canonicalize()?;
        for component in suffix.into_iter().rev() {
            resolved.push(component);
        }
        if self
            .workspaces
            .values()
            .any(|workspace| resolved.starts_with(&workspace.root))
        {
            return Err(StartupError::Invalid(
                "audit file must be outside all workspaces",
            ));
        }
        Ok(resolved)
    }
}
