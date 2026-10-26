//! Prepare a fresh development fixture without overwriting an existing directory.

use serde_json::json;
use std::{fs, path::PathBuf};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let directory = PathBuf::from(
        std::env::args()
            .nth(1)
            .unwrap_or_else(|| ".agentfence".into()),
    );
    // create_dir deliberately fails if this directory exists; never overwrite a
    // user's prior fixture or recursively copy into an arbitrary existing tree.
    fs::create_dir(&directory)?;
    fs::create_dir(directory.join("workspace"))?;
    fs::create_dir(directory.join("workspace/src"))?;
    fs::write(
        directory.join("workspace/src/main.rs"),
        "fn main() { println!(\"before\"); }\n",
    )?;
    fs::write(
        directory.join("workspace/Cargo.toml"),
        "[package]\nname = \"demo-target\"\nversion = \"0.1.0\"\nedition = \"2024\"\n\n[workspace]\n",
    )?;
    fs::write(
        directory.join("simulated-private.txt"),
        "SIMULATED_TEST_TEXT_ONLY\n",
    )?;
    let policy = json!({"workspaces":{"demo":{"root":"workspace","allowed_paths":["src/main.rs","Cargo.toml"],"max_content_bytes":65536}}});
    fs::write(
        directory.join("policy.json"),
        serde_json::to_vec_pretty(&policy)?,
    )?;
    println!("Prepared independent fixture in {}", directory.display());
    println!("Policy: {}", directory.join("policy.json").display());
    Ok(())
}
