//! Physical path inspection for a trusted, static workspace.
//!
//! Component checks and canonicalization are not race-free file opens. The
//! broker cannot defend against malicious concurrent filesystem replacement,
//! hard links introduced by an attacker, or direct host filesystem access.

use crate::ReasonCode;
use std::{
    fs, io,
    path::{Path, PathBuf},
};

/// A path inspection failure, keeping missing files separate from policy denial.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum PathError {
    /// Noncanonical, linked, nonregular or out-of-root resource.
    #[error("path denied")]
    Denied,
    /// An allowed path or its parent is absent.
    #[error("allowed file not found")]
    Missing,
    /// Metadata or canonicalization failed for another reason.
    #[error("file metadata unavailable")]
    Io,
}

impl PathError {
    /// Stable safe error mapping; never includes an absolute host path.
    #[must_use]
    pub const fn reason(self) -> ReasonCode {
        match self {
            Self::Denied => ReasonCode::PathDenied,
            Self::Missing => ReasonCode::FileNotFound,
            Self::Io => ReasonCode::IoError,
        }
    }
}

/// Validate the protocol's portable slash-separated relative path grammar.
///
/// Reject empty segments, dot segments, drive prefixes, control characters,
/// backslashes and wildcard/device aliases. Do not URL-decode JSON paths.
pub fn validate_relative(path: &str) -> Result<(), ReasonCode> {
    if path.is_empty()
        || path.len() > 1024
        || path
            .chars()
            .any(|c| c.is_control() || "\\:*?<>|\"".contains(c))
    {
        return Err(ReasonCode::PathDenied);
    }
    for segment in path.split('/') {
        if segment.is_empty() || segment == "." || segment == ".." || segment.ends_with(['.', ' '])
        {
            return Err(ReasonCode::PathDenied);
        }
        let base = segment.split('.').next().unwrap_or("").to_ascii_uppercase();
        if ["CON", "PRN", "AUX", "NUL"].contains(&base.as_str())
            || (base.len() == 4
                && (base.starts_with("COM") || base.starts_with("LPT"))
                && matches!(base.as_bytes()[3], b'1'..=b'9'))
        {
            return Err(ReasonCode::PathDenied);
        }
    }
    Ok(())
}

fn map_io(error: io::Error) -> PathError {
    if error.kind() == io::ErrorKind::NotFound {
        PathError::Missing
    } else {
        PathError::Io
    }
}

fn linked(metadata: &fs::Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return true;
        }
    }
    false
}

/// Resolve an existing regular file under an already-canonical workspace root.
///
/// Inspect the root, every intermediate directory and the final target without
/// following symlinks (including Windows reparse points). Compare canonical
/// path components rather than string prefixes. Missing files return Missing,
/// leaving the caller to report an allowed-but-unexecuted I/O outcome.
pub fn resolve_existing(root: &Path, relative: &str) -> Result<PathBuf, PathError> {
    validate_relative(relative).map_err(|_| PathError::Denied)?;
    let root_meta = fs::symlink_metadata(root).map_err(map_io)?;
    if linked(&root_meta) || !root_meta.is_dir() {
        return Err(PathError::Denied);
    }
    let mut current = root.to_path_buf();
    let mut segments = relative.split('/').peekable();
    while let Some(segment) = segments.next() {
        current.push(segment);
        let metadata = fs::symlink_metadata(&current).map_err(map_io)?;
        if linked(&metadata) {
            return Err(PathError::Denied);
        }
        if segments.peek().is_some() {
            if !metadata.is_dir() {
                return Err(PathError::Denied);
            }
        } else if !metadata.is_file() {
            return Err(PathError::Denied);
        }
    }
    let canonical = current.canonicalize().map_err(map_io)?;
    if canonical == root || !canonical.starts_with(root) {
        return Err(PathError::Denied);
    }
    Ok(canonical)
}
