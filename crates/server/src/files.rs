//! Bounded UTF-8 reads and replacement writes in a trusted static fixture.

use agentfence_core::ReasonCode;
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::Path,
};

/// Trusted file-execution dependency, separated for deterministic fault tests.
///
/// Implementations must report failure only when replacement did not happen.
/// This API is not selectable through HTTP or configuration.
pub trait FileOperations: Send + Sync {
    /// Return bounded UTF-8 text, or a stable safe failure code.
    fn read(&self, path: &Path, limit: usize) -> Result<String, ReasonCode>;
    /// Replace an existing file completely, preserving required permissions.
    fn write(&self, path: &Path, content: &str) -> Result<usize, ReasonCode>;
}

/// Standard-library reads and same-directory tempfile replacement.
#[derive(Default)]
pub struct LocalFiles;

impl FileOperations for LocalFiles {
    fn read(&self, path: &Path, limit: usize) -> Result<String, ReasonCode> {
        let file = File::open(path).map_err(map_io)?;
        let mut bytes = Vec::new();
        file.take(limit as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(map_io)?;
        if bytes.len() > limit {
            return Err(ReasonCode::PayloadTooLarge);
        }
        String::from_utf8(bytes).map_err(|_| ReasonCode::NonUtf8Content)
    }

    fn write(&self, path: &Path, content: &str) -> Result<usize, ReasonCode> {
        replace(path, content, || Ok(()))
    }
}

fn replace(
    path: &Path,
    content: &str,
    before_replace: impl FnOnce() -> std::io::Result<()>,
) -> Result<usize, ReasonCode> {
    let original = fs::metadata(path).map_err(map_io)?;
    if !original.is_file() {
        return Err(ReasonCode::PathDenied);
    }
    let parent = path.parent().ok_or(ReasonCode::IoError)?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent).map_err(map_io)?;
    temporary.write_all(content.as_bytes()).map_err(map_io)?;
    temporary
        .as_file()
        .set_permissions(original.permissions())
        .map_err(map_io)?;
    temporary.as_file().sync_all().map_err(map_io)?;
    before_replace().map_err(map_io)?;
    // persist performs same-filesystem replacement. On an error the owned
    // NamedTempFile is dropped and cleaned up; the target was not replaced.
    temporary.persist(path).map_err(|_| ReasonCode::IoError)?;
    Ok(content.len())
}

fn map_io(error: std::io::Error) -> ReasonCode {
    if error.kind() == std::io::ErrorKind::NotFound {
        ReasonCode::FileNotFound
    } else {
        ReasonCode::IoError
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn failure_after_temporary_write_cleans_up_without_truncation() {
        let directory = tempfile::tempdir().unwrap();
        let target = directory.path().join("file.rs");
        fs::write(&target, "original").unwrap();
        assert_eq!(
            replace(&target, "replacement", || Err(std::io::Error::other(
                "injected before rename"
            ))),
            Err(ReasonCode::IoError)
        );
        assert_eq!(fs::read_to_string(&target).unwrap(), "original");
        assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
    }

    #[cfg(unix)]
    #[test]
    fn successful_replacement_preserves_unix_permissions() {
        use std::os::unix::fs::PermissionsExt;
        let directory = tempfile::tempdir().unwrap();
        let target = directory.path().join("file.rs");
        fs::write(&target, "old").unwrap();
        fs::set_permissions(&target, fs::Permissions::from_mode(0o640)).unwrap();
        LocalFiles.write(&target, "new").unwrap();
        assert_eq!(
            fs::metadata(target).unwrap().permissions().mode() & 0o777,
            0o640
        );
    }
}
