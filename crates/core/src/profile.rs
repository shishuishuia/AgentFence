//! Immutable task capabilities, independent of the current lifecycle stage.

use serde::{Deserialize, Serialize};

/// Chosen by an administrator at task creation and never upgraded in place.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PermissionProfile {
    /// Inspect and verify files; edits and write grants are always forbidden.
    Readonly,
    /// Legacy four-stage workflow with explicit, bounded write grants.
    #[default]
    ControlledEdit,
}

#[cfg(test)]
mod tests {
    use super::PermissionProfile;
    use crate::Phase;

    #[test]
    fn readonly_lifecycle_never_enters_edit() {
        let profile = PermissionProfile::Readonly;
        assert_eq!(Phase::Inspect.next_for(profile), Some(Phase::Verify));
        assert_eq!(Phase::Verify.next_for(profile), Some(Phase::Done));
        assert_eq!(Phase::Done.next_for(profile), None);
        assert_eq!(Phase::Edit.next_for(profile), None);
        assert!(serde_json::from_str::<PermissionProfile>("\"admin\"").is_err());
    }
}
