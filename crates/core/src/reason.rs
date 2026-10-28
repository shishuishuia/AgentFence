//! Stable machine-readable outcomes shared by core rules and HTTP adapters.

use serde::Serialize;

/// A stable outcome. Clients should use this enum's JSON spelling, not messages.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ReasonCode {
    /// The operation completed successfully.
    Ok,
    /// An audit intent was recorded; authorization/execution is not final.
    Pending,
    /// Invalid JSON, fields, ranges or an unregistered workspace.
    InvalidRequest,
    /// Only read_file and write_file are supported.
    UnknownTool,
    /// The bearer credential is absent or not recognized.
    InvalidToken,
    /// A recognized credential has the wrong role.
    RoleDenied,
    /// An Agent credential belongs to a different task.
    TaskTokenMismatch,
    /// The current task stage forbids this tool.
    InvalidStage,
    /// The immutable task profile forbids editing or issuing write grants.
    ReadonlyTask,
    /// A workspace preview expired, was consumed or is unknown.
    PreviewExpired,
    /// Source contents changed after administrator preview.
    SnapshotChanged,
    /// A configured import/traversal/count/size budget was exceeded.
    ImportLimitExceeded,
    /// The path is not an exact allowed regular-file resource.
    PathDenied,
    /// The write request did not supply a grant ID.
    LeaseRequired,
    /// The supplied grant does not exist within this task.
    LeaseNotFound,
    /// The grant is bound to a different tool or path.
    LeaseMismatch,
    /// The grant was explicitly revoked.
    LeaseRevoked,
    /// The monotonic deadline has been reached.
    LeaseExpired,
    /// There are no remaining successful uses.
    LeaseUsedUp,
    /// No task exists for an authorized administrator query.
    TaskNotFound,
    /// An allowed file or one of its parent directories is absent.
    FileNotFound,
    /// A requested stage is not the immediately following stage.
    InvalidTransition,
    /// A usable grant already exists for this task, tool and path.
    GrantConflict,
    /// The request body or UTF-8 content exceeds its byte limit.
    PayloadTooLarge,
    /// An allowed file contains invalid UTF-8.
    NonUtf8Content,
    /// An expected filesystem operation failed.
    IoError,
    /// Audit logging has failed; tool execution is disabled until restart.
    AuditUnavailable,
    /// A route does not exist.
    NotFound,
    /// A known route does not support the requested method.
    MethodNotAllowed,
}

impl ReasonCode {
    /// Return the public HTTP status associated with an outcome.
    #[must_use]
    pub const fn status(self) -> u16 {
        match self {
            Self::Ok | Self::Pending => 200,
            Self::InvalidRequest | Self::UnknownTool => 400,
            Self::InvalidToken => 401,
            Self::RoleDenied
            | Self::TaskTokenMismatch
            | Self::InvalidStage
            | Self::ReadonlyTask
            | Self::PathDenied
            | Self::LeaseRequired
            | Self::LeaseNotFound
            | Self::LeaseMismatch
            | Self::LeaseRevoked
            | Self::LeaseExpired
            | Self::LeaseUsedUp => 403,
            Self::TaskNotFound | Self::FileNotFound | Self::NotFound => 404,
            Self::MethodNotAllowed => 405,
            Self::InvalidTransition
            | Self::GrantConflict
            | Self::PreviewExpired
            | Self::SnapshotChanged => 409,
            Self::PayloadTooLarge | Self::ImportLimitExceeded => 413,
            Self::NonUtf8Content => 422,
            Self::IoError => 500,
            Self::AuditUnavailable => 503,
        }
    }

    /// Return a safe explanation that never contains host paths or credentials.
    #[must_use]
    pub const fn message(self) -> &'static str {
        match self {
            Self::Ok => "operation completed",
            Self::Pending => "intent recorded; execution is not confirmed",
            Self::InvalidRequest => "invalid request fields or parameter range",
            Self::UnknownTool => "unsupported tool",
            Self::InvalidToken => "missing or invalid bearer token",
            Self::RoleDenied => "token role cannot access this endpoint",
            Self::TaskTokenMismatch => "agent token is bound to another task",
            Self::InvalidStage => "tool is not permitted in the current phase",
            Self::ReadonlyTask => "readonly tasks cannot edit or receive write grants",
            Self::PreviewExpired => "workspace preview expired, consumed or unknown; preview again",
            Self::SnapshotChanged => "source changed since approval preview; preview again",
            Self::ImportLimitExceeded => {
                "workspace import exceeds a file, byte, entry or preview budget"
            }
            Self::PathDenied => "path is not an allowed regular file",
            Self::LeaseRequired => "write_file requires an explicit grant_id",
            Self::LeaseNotFound => "grant not found in this task",
            Self::LeaseMismatch => "grant is bound to a different resource",
            Self::LeaseRevoked => "grant was revoked",
            Self::LeaseExpired => "grant has expired",
            Self::LeaseUsedUp => "grant has no remaining uses",
            Self::TaskNotFound => "task not found",
            Self::FileNotFound => "allowed file not found",
            Self::InvalidTransition => "only the next phase is permitted",
            Self::GrantConflict => "a usable grant already exists for this resource",
            Self::PayloadTooLarge => "request or file exceeds the byte limit",
            Self::NonUtf8Content => "file is not UTF-8 text",
            Self::IoError => "file operation failed",
            Self::AuditUnavailable => {
                "audit unavailable; inspect state and restart; do not retry writes automatically"
            }
            Self::NotFound => "endpoint not found",
            Self::MethodNotAllowed => "method not allowed",
        }
    }
}

impl std::fmt::Display for ReasonCode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.message())
    }
}

impl std::error::Error for ReasonCode {}
