//! Authenticated HTTP tool broker for trusted static workspaces.
//!
//! The core supplies deterministic rules; this crate supplies HTTP validation,
//! task-serialized I/O, intent/result audit and read-only snapshots. This is a
//! tool gateway, not an operating-system sandbox.

pub mod audit;
pub mod broker;
pub mod clock;
pub mod config;
pub mod files;
mod http;

pub use broker::Broker;
pub use config::{Configuration, StartupError};
pub use http::app;
