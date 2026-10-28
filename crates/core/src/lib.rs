//! Deterministic authorization rules for the AgentFence tool broker.
//!
//! Pure policy decisions are separate from filesystem path inspection. Callers
//! must hold a task's execution lock across authorization, I/O and lease use.
//!
//! ```
//! use agentfence_core::Phase;
//!
//! assert_eq!(Phase::Inspect.next(), Some(Phase::Edit));
//! assert_eq!(Phase::Done.next(), None);
//! ```

pub mod grant;
pub mod path;
mod phase;
pub mod policy;
mod profile;
pub mod reason;
pub mod time;

pub use grant::{Grant, GrantSpec, GrantStatus};
pub use path::{PathError, resolve_existing, validate_relative};
pub use phase::Phase;
pub use policy::{Policy, Tool, ToolCall};
pub use profile::PermissionProfile;
pub use reason::ReasonCode;
pub use time::TimePoint;
