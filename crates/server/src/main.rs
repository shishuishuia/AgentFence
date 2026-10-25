//! Start the local AgentFence HTTP service.

use std::{error::Error, net::SocketAddr};

#[tokio::main]
async fn main() -> Result<(), Box<dyn Error>> {
    let address: SocketAddr = std::env::var("AGENTFENCE_BIND")
        .unwrap_or_else(|_| "127.0.0.1:8080".to_owned())
        .parse()?;

    if !address.ip().is_loopback() {
        return Err("AGENTFENCE_BIND must use a loopback address for the local demo".into());
    }

    let listener = tokio::net::TcpListener::bind(address).await?;
    eprintln!("AgentFence listening on http://{}", listener.local_addr()?);

    axum::serve(listener, agentfence_server::app())
        .with_graceful_shutdown(shutdown_signal())
        .await?;
    Ok(())
}

async fn shutdown_signal() {
    if let Err(error) = tokio::signal::ctrl_c().await {
        eprintln!("Could not install Ctrl+C handler: {error}");
    }
}
