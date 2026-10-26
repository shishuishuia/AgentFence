//! Loopback-only experiment entry using the product HTTP, file and audit path.

use agentfence_server::experiment::{self, Mode};
use serde_json::json;
use std::{error::Error, net::SocketAddr, path::PathBuf};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let mut args = std::env::args().skip(1);
    let mode: Mode = args
        .next()
        .ok_or("usage: http_experiment <enforced|baseline> <new-directory> [127.0.0.1:0]")?
        .parse()?;
    let directory = PathBuf::from(
        args.next()
            .ok_or("a new experiment directory is required")?,
    );
    let address: SocketAddr = args
        .next()
        .unwrap_or_else(|| "127.0.0.1:0".into())
        .parse()?;
    if args.next().is_some() || !address.ip().is_loopback() {
        return Err("expected at most three arguments and a loopback bind address".into());
    }
    let admin = std::env::var("AGENTFENCE_ADMIN_TOKEN")
        .map_err(|_| "AGENTFENCE_ADMIN_TOKEN is required; use a fresh random secret")?;
    let listener = tokio::net::TcpListener::bind(address).await?;
    let router = experiment::prepare(&directory, mode, &admin)?;
    println!(
        "{}",
        json!({"event":"experiment_ready", "mode":mode.name(),
        "base_url":format!("http://{}", listener.local_addr()?),
        "directory":directory.canonicalize()?})
    );
    axum::serve(listener, router)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
