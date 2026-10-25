//! Exercise the real router without binding a TCP port.

use axum::{body::Body, http::Request};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use tower::ServiceExt;

#[tokio::test]
async fn health_check_is_public_and_returns_json() {
    let response = agentfence_server::app()
        .oneshot(Request::get("/healthz").body(Body::empty()).unwrap())
        .await
        .unwrap();

    assert_eq!(response.status(), 200);
    assert_eq!(response.headers()["content-type"], "application/json");
    let body = response.into_body().collect().await.unwrap().to_bytes();
    assert_eq!(
        serde_json::from_slice::<Value>(&body).unwrap(),
        json!({"status": "ok"})
    );
}

#[tokio::test]
async fn unavailable_tool_endpoint_returns_not_found() {
    let response = agentfence_server::app()
        .oneshot(
            Request::post("/v1/tool/execute")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), 404);
    let body = response.into_body().collect().await.unwrap().to_bytes();
    let error: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(error["reason_code"], "NOT_FOUND");
}
