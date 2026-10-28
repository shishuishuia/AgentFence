//! Synthetic review fixture: the caller must validate a nonzero count.

/// Return a whole-number average.
pub fn average(total: u32, count: u32) -> u32 {
    total / count
}
