//! Clock samples: monotonic time enforces TTL, UTC time is display-only.

use chrono::{DateTime, Utc};
use std::time::Duration;

/// One server-generated clock sample, injectable by tests.
///
/// `monotonic` must advance independently of wall-clock corrections. Never
/// construct it from a client's timestamp. UTC is only used in snapshots/logs.
#[derive(Debug, Clone, Copy)]
pub struct TimePoint {
    /// Elapsed monotonic time since this broker instance started.
    pub monotonic: Duration,
    /// Current server UTC wall-clock time for display and audit.
    pub utc: DateTime<Utc>,
}
