//! Feature-gated HTTP experiments in a newly created, fixed-content fixture.
//!
//! The static baseline changes only phase/lease authorization. Authentication,
//! task binding, exact path ACL, physical path checks, I/O, locks and durable
//! audit use the product code. Neither HTTP nor product configuration selects it.

use crate::{Broker, Configuration, StartupError, app, authorization::Authorization};
use axum::Router;
use serde_json::json;
use std::{fs, path::Path, str::FromStr};

/// Authorization treatment for an isolated experiment, fixed at construction.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    /// The same dynamic phase and lease checks as the product.
    Enforced,
    /// Static path ACL with writes enabled throughout the task window.
    Baseline,
}

impl Mode {
    /// Stable manifest/CLI name.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Enforced => "enforced",
            Self::Baseline => "baseline",
        }
    }
}

impl FromStr for Mode {
    type Err = StartupError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "enforced" => Ok(Self::Enforced),
            "baseline" => Ok(Self::Baseline),
            _ => Err(StartupError::Invalid(
                "experiment mode must be enforced or baseline",
            )),
        }
    }
}

/// Create a fresh synthetic fixture and the ordinary authenticated HTTP router.
///
/// `directory` must not exist; its parent must exist. No user policy or existing
/// workspace can be supplied. The caller must bind the router to loopback only.
/// Setup and task/grant preparation must be excluded from benchmark samples.
///
/// The fixed workspace `demo` allows `src/main.rs`, `Cargo.toml` and `missing.rs`.
/// Audit and an experiment manifest are stored outside that workspace. Baseline
/// writes do not use or consume grants: response grant fields are JSON null.
pub fn prepare(directory: &Path, mode: Mode, admin_token: &str) -> Result<Router, StartupError> {
    fs::create_dir(directory)?;
    let root = directory.join("workspace");
    fs::create_dir(&root)?;
    fs::create_dir(root.join("src"))?;
    fs::write(
        root.join("src/main.rs"),
        "fn main() { println!(\"fixture\"); }\n",
    )?;
    fs::write(
        root.join("Cargo.toml"),
        "[package]\nname = \"experiment-target\"\nversion = \"0.1.0\"\nedition = \"2024\"\n\n[workspace]\n",
    )?;
    fs::write(root.join("unlisted.txt"), "SIMULATED_UNLISTED_FILE\n")?;
    let policy = json!({"workspaces":{"demo":{
        "root":"workspace", "allowed_paths":["src/main.rs","Cargo.toml","missing.rs"],
        "max_content_bytes":65536
    }}});
    fs::write(
        directory.join("policy.json"),
        serde_json::to_vec_pretty(&policy)?,
    )?;
    let config = Configuration::from_json(&policy.to_string(), directory)?;
    let authorization = match mode {
        Mode::Enforced => Authorization::Enforced,
        Mode::Baseline => Authorization::Static,
    };
    let broker = Broker::open_with_authorization(
        config,
        admin_token,
        &directory.join("audit.jsonl"),
        authorization,
    )?;
    fs::write(
        directory.join("experiment.json"),
        serde_json::to_vec_pretty(&json!({
            "schema_version":1, "mode":mode.name(), "workspace_id":"demo",
            "authorization": match mode {
                Mode::Enforced => "phase_path_lease",
                Mode::Baseline => "static_path_acl",
            },
            "max_content_bytes":65536, "audit":"FileSink::append + sync_data",
            "file_operations":"LocalFiles", "http":"agentfence_server::app",
            "synthetic_fixture":true
        }))?,
    )?;
    Ok(app(broker))
}
