//! The policy delta shared by the product and the isolated static-ACL experiment.

use agentfence_core::{Grant, Phase, Policy, ReasonCode, ToolCall, policy::check_lease};
use std::time::Duration;

#[derive(Clone, Copy)]
pub(crate) enum Authorization {
    Enforced,
    #[cfg(feature = "bench-harness")]
    Static,
}

pub(crate) enum Permit {
    Read,
    Lease(usize),
    #[cfg(feature = "bench-harness")]
    StaticWrite,
}

impl Permit {
    pub(crate) fn grant_index(&self) -> Option<usize> {
        match self {
            Self::Lease(index) => Some(*index),
            _ => None,
        }
    }

    pub(crate) fn write_grant(&self) -> Result<Option<usize>, ReasonCode> {
        match self {
            Self::Read => Err(ReasonCode::LeaseRequired),
            Self::Lease(index) => Ok(Some(*index)),
            #[cfg(feature = "bench-harness")]
            Self::StaticWrite => Ok(None),
        }
    }
}

impl Authorization {
    pub(crate) fn check_resource(
        self,
        policy: &Policy,
        phase: Phase,
        call: ToolCall<'_>,
    ) -> Result<(), ReasonCode> {
        match self {
            Self::Enforced => policy.check_resource(phase, call),
            #[cfg(feature = "bench-harness")]
            Self::Static => policy.check_path(call.path),
        }
    }

    pub(crate) fn check_lease(
        self,
        task_id: &str,
        grants: &[Grant],
        call: ToolCall<'_>,
        now: Duration,
    ) -> Result<Permit, ReasonCode> {
        match self {
            Self::Enforced => Ok(match check_lease(task_id, grants, call, now)? {
                Some(index) => Permit::Lease(index),
                None => Permit::Read,
            }),
            #[cfg(feature = "bench-harness")]
            Self::Static => Ok(match call.tool {
                agentfence_core::Tool::ReadFile => Permit::Read,
                agentfence_core::Tool::WriteFile => Permit::StaticWrite,
            }),
        }
    }
}
