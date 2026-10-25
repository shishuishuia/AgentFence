//! HTTP entry points for the AgentFence tool broker.
//!
//! This initial server exposes only a public liveness check. Administrative
//! and tool endpoints will be added together with their authorization checks.

use axum::{Json, Router, http::StatusCode, routing::get};
use serde::Serialize;

/// Build the application's HTTP router without opening a network listener.
///
/// Keeping construction separate from listening lets tests send requests
/// directly to the same router used by the binary.
///
/// ```
/// let _app = agentfence_server::app();
/// ```
pub fn app() -> Router {
    Router::new()
        .route("/healthz", get(health))
        .fallback(not_found)
}

#[derive(Serialize)]
struct HealthResponse {
    status: &'static str,
}

async fn health() -> Json<HealthResponse> {
    Json(HealthResponse { status: "ok" })
}

#[derive(Serialize)]
struct ErrorResponse {
    reason_code: &'static str,
    message: &'static str,
}

async fn not_found() -> (StatusCode, Json<ErrorResponse>) {
    (
        StatusCode::NOT_FOUND,
        Json(ErrorResponse {
            reason_code: "NOT_FOUND",
            message: "endpoint not found",
        }),
    )
}
