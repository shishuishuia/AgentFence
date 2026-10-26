//! Pure decisions: no HTTP, filesystem access or logging inside measurements.

use crate::{Grant, Phase, ReasonCode, validate_relative};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, time::Duration};

/// One of the two supported tools.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Tool {
    /// Read an existing UTF-8 file.
    ReadFile,
    /// Replace an existing file using an explicit write lease.
    WriteFile,
}

impl Tool {
    /// Parse the protocol name without defaulting an unknown tool to a known one.
    pub fn parse(name: &str) -> Result<Self, ReasonCode> {
        match name {
            "read_file" => Ok(Self::ReadFile),
            "write_file" => Ok(Self::WriteFile),
            _ => Err(ReasonCode::UnknownTool),
        }
    }
}

/// Resource fields after request-structure validation.
#[derive(Debug, Clone, Copy)]
pub struct ToolCall<'a> {
    /// Requested supported tool.
    pub tool: Tool,
    /// Raw JSON-decoded path; never URL-decode this value.
    pub path: &'a str,
    /// Explicit write lease selection; absent for reads.
    pub grant_id: Option<&'a str>,
}

/// Exact allowed paths plus a bounded UTF-8 content size.
#[derive(Debug, Clone)]
pub struct Policy {
    allowed_paths: BTreeSet<String>,
    max_content_bytes: usize,
}

impl Policy {
    /// Validate an exact, nonempty allowlist and a byte limit in 1..=65536.
    ///
    /// Duplicate paths, wildcard-like paths and invalid limits are configuration
    /// errors. Unknown resource paths remain denied by default.
    pub fn new(paths: Vec<String>, max_content_bytes: usize) -> Result<Self, ReasonCode> {
        if paths.is_empty() || !(1..=65_536).contains(&max_content_bytes) {
            return Err(ReasonCode::InvalidRequest);
        }
        let mut allowed_paths = BTreeSet::new();
        for path in paths {
            validate_relative(&path)?;
            if !allowed_paths.insert(path) {
                return Err(ReasonCode::InvalidRequest);
            }
        }
        Ok(Self {
            allowed_paths,
            max_content_bytes,
        })
    }

    /// Exact relative paths, in deterministic lexical order.
    #[must_use]
    pub fn allowed_paths(&self) -> Vec<String> {
        self.allowed_paths.iter().cloned().collect()
    }

    /// Maximum returned or submitted UTF-8 byte count.
    #[must_use]
    pub const fn max_content_bytes(&self) -> usize {
        self.max_content_bytes
    }

    /// Check phase first, then lexical path syntax and exact membership.
    ///
    /// Physical symlink/regular-file checks belong between this method and
    /// check_lease. A server must not omit those filesystem checks.
    pub fn check_resource(&self, phase: Phase, call: ToolCall<'_>) -> Result<(), ReasonCode> {
        if phase == Phase::Done || (call.tool == Tool::WriteFile && phase != Phase::Edit) {
            return Err(ReasonCode::InvalidStage);
        }
        self.check_path(call.path)
    }

    /// Check a path against this policy independently of task phase.
    pub fn check_path(&self, path: &str) -> Result<(), ReasonCode> {
        validate_relative(path)?;
        if self.allowed_paths.contains(path) {
            Ok(())
        } else {
            Err(ReasonCode::PathDenied)
        }
    }

    /// Make a complete pure decision; return the selected grant index for writes.
    ///
    /// A successful decision is not a filesystem-safety proof or an execution.
    /// It does not mutate lease counters. The broker adds physical path checks
    /// and holds a task lock until execution and use consumption have completed.
    ///
    /// ```
    /// use agentfence_core::{Grant, GrantSpec, Phase, Policy, TimePoint, Tool, ToolCall};
    /// use chrono::Utc;
    /// use std::time::Duration;
    /// let now = TimePoint { monotonic: Duration::ZERO, utc: Utc::now() };
    /// let grant = Grant::issue(GrantSpec {
    ///     grant_id: "g1".into(), task_id: "t1".into(), path: "src/main.rs".into(),
    ///     ttl_seconds: 60, max_uses: 1,
    /// }, now)?;
    /// let policy = Policy::new(vec!["src/main.rs".into()], 65536)?;
    /// let call = ToolCall { tool: Tool::WriteFile, path: "src/main.rs", grant_id: Some("g1") };
    /// assert_eq!(policy.evaluate("t1", Phase::Edit, &[grant], call, now.monotonic)?, Some(0));
    /// # Ok::<(), agentfence_core::ReasonCode>(())
    /// ```
    ///
    /// ```
    /// use agentfence_core::{Phase, Policy, ReasonCode, Tool, ToolCall};
    /// use std::time::Duration;
    /// let policy = Policy::new(vec!["src/main.rs".into()], 65536)?;
    /// let call = ToolCall { tool: Tool::WriteFile, path: "src/main.rs", grant_id: None };
    /// assert_eq!(policy.evaluate("t1", Phase::Verify, &[], call, Duration::ZERO),
    ///            Err(ReasonCode::InvalidStage));
    /// # Ok::<(), agentfence_core::ReasonCode>(())
    /// ```
    pub fn evaluate(
        &self,
        task_id: &str,
        phase: Phase,
        grants: &[Grant],
        call: ToolCall<'_>,
        now: Duration,
    ) -> Result<Option<usize>, ReasonCode> {
        self.check_resource(phase, call)?;
        check_lease(task_id, grants, call, now)
    }
}

/// Check explicit lease selection in protocol order, without touching files.
///
/// Search only within the owning task: another task's ID is LEASE_NOT_FOUND.
/// Within this task check binding, revocation, expiry, then remaining uses.
pub fn check_lease(
    task_id: &str,
    grants: &[Grant],
    call: ToolCall<'_>,
    now: Duration,
) -> Result<Option<usize>, ReasonCode> {
    if call.tool == Tool::ReadFile {
        return Ok(None);
    }
    let id = call.grant_id.ok_or(ReasonCode::LeaseRequired)?;
    let (index, grant) = grants
        .iter()
        .enumerate()
        .find(|(_, g)| g.grant_id == id && g.task_id == task_id)
        .ok_or(ReasonCode::LeaseNotFound)?;
    if grant.tool != call.tool || grant.path != call.path {
        return Err(ReasonCode::LeaseMismatch);
    }
    if grant.revoked {
        return Err(ReasonCode::LeaseRevoked);
    }
    if grant.expired(now) {
        return Err(ReasonCode::LeaseExpired);
    }
    if grant.remaining_uses == 0 {
        return Err(ReasonCode::LeaseUsedUp);
    }
    Ok(Some(index))
}
