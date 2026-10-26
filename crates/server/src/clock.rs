//! Injectable monotonic/UTC clock with no client-supplied time inputs.

use agentfence_core::TimePoint;
use chrono::Utc;
use std::time::Instant;

/// Trusted clock dependency; tests may supply an exact deterministic clock.
pub trait Clock: Send + Sync {
    /// Take one sample. Monotonic time governs TTL; UTC is for display only.
    fn sample(&self) -> TimePoint;
}

/// Real clock anchored to this process instance.
pub struct SystemClock {
    origin: Instant,
}

impl Default for SystemClock {
    fn default() -> Self {
        Self {
            origin: Instant::now(),
        }
    }
}

impl Clock for SystemClock {
    fn sample(&self) -> TimePoint {
        TimePoint {
            monotonic: self.origin.elapsed(),
            utc: Utc::now(),
        }
    }
}
