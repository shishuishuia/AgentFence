//! Run only the MCP stdio transport; diagnostics never use stdout.

use agentfence_mcp_server::McpServer;
use rmcp::{
    ServiceExt,
    transport::{async_rw::AsyncRwTransport, stdio},
};

#[tokio::main]
async fn main() {
    if run().await.is_err() {
        // Do not print SDK/HTTP error Debug: it can contain local configuration.
        eprintln!(
            "AgentFence MCP stopped; check task credentials, broker health and client protocol"
        );
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let server = McpServer::from_env().await?;
    let (input, output) = stdio();
    let transport = AsyncRwTransport::new(input, output).with_max_line_length(512 * 1024);
    let service = server.serve(transport).await?;
    service.waiting().await?;
    Ok(())
}
