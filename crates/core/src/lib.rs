//! Task phases and, in later steps, authorization policy for AgentFence.
//!
//! The core contains no HTTP handlers or file operations, so policy rules can
//! be tested independently of the server.
//!
//! ```
//! use agentfence_core::Phase;
//!
//! assert_eq!(Phase::Inspect.next(), Some(Phase::Edit));
//! assert_eq!(Phase::Done.next(), None);
//! ```

mod phase;

pub use phase::Phase;
