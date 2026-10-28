//! Bounded, administrator-approved imports into instance-owned file snapshots.
//!
//! This reduces live-project mutation exposure; it is not an OS sandbox or a
//! race-free import against a hostile process with the same host privileges.

use crate::config::Workspace;
use agentfence_core::{Policy, ReasonCode, resolve_existing, validate_relative};
use chrono::{DateTime, Utc};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};

pub(crate) const MAX_FILES: usize = 1000;
pub(crate) const MAX_FILE_BYTES: usize = 65_536;
pub(crate) const MAX_TOTAL_BYTES: usize = 20 * 1024 * 1024;
const MAX_ENTRIES: usize = 20_000;
pub(crate) const PREVIEW_TTL: Duration = Duration::from_secs(300);

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub(crate) struct FileEntry {
    pub path: String,
    pub bytes: usize,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub(crate) struct ExcludedEntry {
    pub path: String,
    pub reason: &'static str,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub(crate) struct Manifest {
    pub files: Vec<FileEntry>,
    pub excluded: Vec<ExcludedEntry>,
    pub total_bytes: usize,
}
impl Manifest {
    pub fn sha256(&self) -> String {
        // The structure contains only deterministic scalar fields and vectors.
        format!(
            "{:x}",
            Sha256::digest(serde_json::to_vec(self).expect("serializable manifest"))
        )
    }
}

pub(crate) struct Preview {
    pub source: PathBuf,
    pub manifest: Manifest,
    pub deadline: Duration,
    pub expires_at: DateTime<Utc>,
}

#[derive(Debug)]
pub(crate) struct Snapshot {
    pub id: String,
    pub display_name: String,
    pub source: PathBuf,
    pub imported_at: DateTime<Utc>,
    pub manifest: Manifest,
    pub _storage: tempfile::TempDir,
}

pub(crate) struct Scan {
    pub source: PathBuf,
    pub manifest: Manifest,
    contents: Vec<Vec<u8>>,
}

fn linked(meta: &fs::Metadata) -> bool {
    if meta.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if meta.file_attributes() & 0x400 != 0 {
            return true;
        }
    }
    false
}

fn source_root(source: &Path) -> Result<PathBuf, ReasonCode> {
    if !source.is_absolute()
        || source.components().any(|c| {
            matches!(
                c,
                std::path::Component::ParentDir | std::path::Component::CurDir
            )
        })
    {
        return Err(ReasonCode::PathDenied);
    }
    for ancestor in source.ancestors() {
        let meta = fs::symlink_metadata(ancestor).map_err(|_| ReasonCode::IoError)?;
        if linked(&meta) || !meta.is_dir() {
            return Err(ReasonCode::PathDenied);
        }
    }
    source.canonicalize().map_err(|_| ReasonCode::IoError)
}

fn excluded_name(name: &str) -> bool {
    let name = name.to_ascii_lowercase();
    name.starts_with('.')
        || [
            "target",
            "node_modules",
            "vendor",
            "dist",
            "build",
            "__pycache__",
        ]
        .contains(&name.as_str())
        || [
            "credential",
            "secret",
            "private_key",
            "private-key",
            "api_key",
            "apikey",
            "service_account",
            "service-account",
            "id_rsa",
            "id_ed25519",
            "kubeconfig",
        ]
        .iter()
        .any(|s| name.contains(s))
}

fn supported(path: &Path) -> bool {
    path.extension().and_then(|s| s.to_str()).is_some_and(|e| {
        ["rs", "toml", "md", "txt", "json", "yaml", "yml"]
            .contains(&e.to_ascii_lowercase().as_str())
    })
}

/// Scan without returning raw file content to the management API.
pub(crate) fn scan(source: &Path) -> Result<Scan, ReasonCode> {
    let root = source_root(source)?;
    let mut scan = Scan {
        source: root.clone(),
        manifest: Manifest {
            files: Vec::new(),
            excluded: Vec::new(),
            total_bytes: 0,
        },
        contents: Vec::new(),
    };
    let mut visited = 0;
    walk(&root, &root, 0, &mut visited, &mut scan)?;
    // A stable manifest and matching content order support repeatable approval.
    let mut paired: Vec<_> = scan.manifest.files.into_iter().zip(scan.contents).collect();
    paired.sort_by(|a, b| a.0.path.cmp(&b.0.path));
    (scan.manifest.files, scan.contents) = paired.into_iter().unzip();
    scan.manifest.excluded.sort_by(|a, b| a.path.cmp(&b.path));
    if scan.manifest.files.is_empty() {
        return Err(ReasonCode::InvalidRequest);
    }
    Ok(scan)
}

fn walk(
    root: &Path,
    dir: &Path,
    depth: usize,
    visited: &mut usize,
    scan: &mut Scan,
) -> Result<(), ReasonCode> {
    if depth > 64 {
        return Err(ReasonCode::ImportLimitExceeded);
    }
    let mut entries = Vec::new();
    for entry in fs::read_dir(dir).map_err(|_| ReasonCode::IoError)? {
        *visited += 1;
        if *visited > MAX_ENTRIES {
            return Err(ReasonCode::ImportLimitExceeded);
        }
        entries.push(entry.map_err(|_| ReasonCode::IoError)?);
    }
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let path = entry.path();
        let Some(relative) = path
            .strip_prefix(root)
            .ok()
            .and_then(|p| p.to_str())
            .map(|s| s.replace('\\', "/"))
        else {
            scan.manifest.excluded.push(ExcludedEntry {
                path: "<non-utf8-name>".into(),
                reason: "non_utf8_path",
            });
            continue;
        };
        let name = entry.file_name();
        let mut exclude = |reason| {
            scan.manifest.excluded.push(ExcludedEntry {
                path: relative.clone(),
                reason,
            })
        };
        if validate_relative(&relative).is_err() {
            exclude("invalid_path");
            continue;
        }
        if name.to_str().is_none_or(excluded_name) {
            exclude("excluded_name");
            continue;
        }
        let meta = fs::symlink_metadata(&path).map_err(|_| ReasonCode::IoError)?;
        if linked(&meta) {
            exclude("link_or_reparse_point");
            continue;
        }
        if meta.is_dir() {
            walk(root, &path, depth + 1, visited, scan)?;
            continue;
        }
        if !meta.is_file() {
            exclude("special_file");
            continue;
        }
        if !supported(&path) {
            exclude("unsupported_extension");
            continue;
        }
        if meta.len() > MAX_FILE_BYTES as u64 || scan.manifest.files.len() >= MAX_FILES {
            return Err(ReasonCode::ImportLimitExceeded);
        }
        let resolved = resolve_existing(root, &relative).map_err(|e| e.reason())?;
        let mut bytes = Vec::new();
        fs::File::open(resolved)
            .map_err(|_| ReasonCode::IoError)?
            .take(MAX_FILE_BYTES as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| ReasonCode::IoError)?;
        if bytes.len() > MAX_FILE_BYTES {
            return Err(ReasonCode::ImportLimitExceeded);
        }
        if std::str::from_utf8(&bytes).is_err() {
            exclude("non_utf8_content");
            continue;
        }
        if scan.manifest.total_bytes + bytes.len() > MAX_TOTAL_BYTES {
            return Err(ReasonCode::ImportLimitExceeded);
        }
        scan.manifest.total_bytes += bytes.len();
        scan.manifest.files.push(FileEntry {
            path: relative,
            bytes: bytes.len(),
            sha256: format!("{:x}", Sha256::digest(&bytes)),
        });
        scan.contents.push(bytes);
    }
    Ok(())
}

pub(crate) fn import(
    preview: Preview,
    name: String,
    snapshot_id: String,
    now: DateTime<Utc>,
) -> Result<Workspace, ReasonCode> {
    let scanned = scan(&preview.source)?;
    if scanned.manifest != preview.manifest {
        return Err(ReasonCode::SnapshotChanged);
    }
    let storage = tempfile::Builder::new()
        .prefix("agentfence-snapshot-")
        .tempdir()
        .map_err(|_| ReasonCode::IoError)?;
    let root = storage.path().join("workspace");
    fs::create_dir(&root).map_err(|_| ReasonCode::IoError)?;
    for (file, bytes) in scanned.manifest.files.iter().zip(&scanned.contents) {
        let target = root.join(&file.path);
        fs::create_dir_all(target.parent().ok_or(ReasonCode::IoError)?)
            .map_err(|_| ReasonCode::IoError)?;
        // Copy exactly the bytes whose hash was approved; later source edits do
        // not change this instance-owned workspace. Broker forbids all writes.
        fs::write(target, bytes).map_err(|_| ReasonCode::IoError)?;
    }
    let policy = Policy::new(
        scanned
            .manifest
            .files
            .iter()
            .map(|f| f.path.clone())
            .collect(),
        MAX_FILE_BYTES,
    )?;
    Ok(Workspace {
        root: root.canonicalize().map_err(|_| ReasonCode::IoError)?,
        policy,
        gate: Arc::new(Mutex::new(())),
        snapshot: Some(Arc::new(Snapshot {
            id: snapshot_id,
            display_name: name,
            source: scanned.source,
            imported_at: now,
            manifest: scanned.manifest,
            _storage: storage,
        })),
    })
}
