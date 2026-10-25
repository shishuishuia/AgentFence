//! The ordered lifecycle of a coding task.

use serde::{Deserialize, Serialize};

/// A task's current stage, encoded as a lowercase string in JSON.
///
/// A later administrator endpoint will own transitions. Agent tool calls must
/// never be allowed to choose or advance this stage.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    /// Inspect allowlisted source files before editing.
    Inspect,
    /// Edit an allowlisted file with a valid write lease.
    Edit,
    /// Read the result to verify a completed edit.
    Verify,
    /// Finish the task; no further tool operations are permitted.
    Done,
}

impl Phase {
    /// Return the only permitted next stage, or `None` for a completed task.
    ///
    /// This describes ordering only; it does not authorize a transition.
    ///
    /// ```
    /// use agentfence_core::Phase;
    ///
    /// assert_eq!(Phase::Edit.next(), Some(Phase::Verify));
    /// assert_eq!(Phase::Verify.next(), Some(Phase::Done));
    /// ```
    #[must_use]
    pub const fn next(self) -> Option<Self> {
        match self {
            Self::Inspect => Some(Self::Edit),
            Self::Edit => Some(Self::Verify),
            Self::Verify => Some(Self::Done),
            Self::Done => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Phase;

    #[test]
    fn lifecycle_has_exactly_four_ordered_stages() {
        let mut current = Some(Phase::Inspect);
        for expected in [Phase::Inspect, Phase::Edit, Phase::Verify, Phase::Done] {
            assert_eq!(current, Some(expected));
            current = current.and_then(Phase::next);
        }
        assert_eq!(current, None);
    }

    #[test]
    fn json_uses_the_contract_stage_names() {
        for (phase, json) in [
            (Phase::Inspect, "\"inspect\""),
            (Phase::Edit, "\"edit\""),
            (Phase::Verify, "\"verify\""),
            (Phase::Done, "\"done\""),
        ] {
            assert_eq!(serde_json::to_string(&phase).unwrap(), json);
            assert_eq!(serde_json::from_str::<Phase>(json).unwrap(), phase);
        }
    }

    #[test]
    fn unknown_stages_cannot_be_deserialized() {
        for json in ["\"admin\"", "\"Inspect\"", "\"\"", "null", "1"] {
            assert!(serde_json::from_str::<Phase>(json).is_err());
        }
    }
}
