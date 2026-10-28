//! Start the local AgentFence HTTP service.

use agentfence_server::{Broker, Configuration};
use std::path::Path;
use std::{error::Error, net::SocketAddr};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let address: SocketAddr = std::env::var("AGENTFENCE_BIND")
        .unwrap_or_else(|_| "127.0.0.1:8080".to_owned())
        .parse()?;

    if !address.ip().is_loopback() {
        return Err("AGENTFENCE_BIND must use a loopback address for the local demo".into());
    }

    let admin = std::env::var("AGENTFENCE_ADMIN_TOKEN")
        .map_err(|_| "AGENTFENCE_ADMIN_TOKEN is required; generate a fresh random 32-byte secret outside the repository")?;
    let policy_path =
        std::env::var("AGENTFENCE_POLICY").unwrap_or_else(|_| "config/policy.json".into());
    let audit_path =
        std::env::var("AGENTFENCE_AUDIT").unwrap_or_else(|_| "results/audit.jsonl".into());
    let config = if std::env::var("AGENTFENCE_EMPTY_REGISTRY").as_deref() == Ok("1") {
        Configuration::empty()
    } else {
        Configuration::load(Path::new(&policy_path))?
    };
    let broker = Broker::open(config, &admin, Path::new(&audit_path))?;
    let listener = tokio::net::TcpListener::bind(address).await?;
    eprintln!("AgentFence listening on http://{}", listener.local_addr()?);

    axum::serve(listener, agentfence_server::app(broker))
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    Ok(())
}

async fn shutdown_signal() {
    if let Err(error) = tokio::signal::ctrl_c().await {
        eprintln!("Could not install Ctrl+C handler: {error}");
    }
}
