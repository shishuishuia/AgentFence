//! Compare the shared HTTP/I/O/audit path with only phase/lease rules changed.
#![cfg(feature = "bench-harness")]

use agentfence_server::{
    Broker, Configuration, app,
    experiment::{self, Mode},
};
use axum::{Router, body::Body, http::Request};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use std::{fs, path::PathBuf};
use tower::ServiceExt;

const ADMIN: &str = "experiment-test-admin-credential-not-for-production";

async fn send(router: &Router, method: &str, path: &str, token: &str, body: Value) -> (u16, Value) {
    let response = router
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(path)
                .header("authorization", format!("Bearer {token}"))
                .header("content-type", "application/json")
                .body(Body::from(if body.is_null() {
                    String::new()
                } else {
                    body.to_string()
                }))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status().as_u16();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap())
}

struct Fixture {
    _temp: tempfile::TempDir,
    directory: PathBuf,
    router: Router,
    task: Value,
}

impl Fixture {
    async fn new(mode: Mode) -> Self {
        let temp = tempfile::tempdir().unwrap();
        let directory = temp.path().join("experiment");
        let router = experiment::prepare(&directory, mode, ADMIN).unwrap();
        let (status, task) = send(
            &router,
            "POST",
            "/v1/admin/tasks",
            ADMIN,
            json!({"workspace_id":"demo"}),
        )
        .await;
        assert_eq!(status, 201, "{task}");
        Self {
            _temp: temp,
            directory,
            router,
            task,
        }
    }

    async fn tool(
        &self,
        tool: &str,
        path: &str,
        content: Option<&str>,
        grant: Option<&str>,
    ) -> (u16, Value) {
        let mut body = json!({"task_id":self.task["task_id"],"tool":tool,"path":path});
        if let Some(content) = content {
            body["content"] = json!(content);
        }
        if let Some(grant) = grant {
            body["grant_id"] = json!(grant);
        }
        send(
            &self.router,
            "POST",
            "/v1/tool/execute",
            self.task["agent_token"].as_str().unwrap(),
            body,
        )
        .await
    }

    async fn admin(&self, suffix: &str, body: Value) -> (u16, Value) {
        send(
            &self.router,
            "POST",
            &format!(
                "/v1/admin/tasks/{}{suffix}",
                self.task["task_id"].as_str().unwrap()
            ),
            ADMIN,
            body,
        )
        .await
    }
}

#[tokio::test]
async fn both_treatments_share_auth_path_limits_file_io_and_durable_audit() {
    for mode in [Mode::Enforced, Mode::Baseline] {
        let f = Fixture::new(mode).await;
        let initial = fs::read_to_string(f.directory.join("workspace/src/main.rs")).unwrap();
        let (status, read) = f.tool("read_file", "src/main.rs", None, None).await;
        assert_eq!(status, 200);
        assert_eq!(read["data"]["content"], initial);
        assert_eq!(read["data"]["bytes_read"], initial.len());
        let records: Vec<Value> = fs::read_to_string(f.directory.join("audit.jsonl"))
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(records.len(), 3);
        assert_eq!(records[1]["event_type"], "intent");
        assert_eq!(records[1]["reason_code"], "PENDING");
        assert_eq!(records[2]["event_type"], "result");
        assert_eq!(records[2]["request_id"], read["request_id"]);
        assert_eq!(records[2]["executed"], true);
        for path in [
            "unlisted.txt",
            "../outside",
            "/absolute",
            "src/../Cargo.toml",
        ] {
            let (status, value) = f.tool("read_file", path, None, None).await;
            assert_eq!(status, 403);
            assert_eq!(value["reason_code"], "PATH_DENIED");
            assert_eq!(value["executed"], false);
        }
        let (status, missing) = f.tool("read_file", "missing.rs", None, None).await;
        assert_eq!(status, 404);
        assert_eq!(missing["reason_code"], "FILE_NOT_FOUND");
        let (status, oversized) = f
            .tool("write_file", "src/main.rs", Some(&"x".repeat(65_537)), None)
            .await;
        assert_eq!(status, 413);
        assert_eq!(oversized["executed"], false);
        let (status, _) = send(
            &f.router,
            "POST",
            "/v1/tool/execute",
            "invalid-token",
            json!({}),
        )
        .await;
        assert_eq!(status, 401);
        let (status, other) = send(
            &f.router,
            "POST",
            "/v1/tool/execute",
            f.task["agent_token"].as_str().unwrap(),
            json!({"task_id":"other","tool":"read_file","path":"src/main.rs"}),
        )
        .await;
        assert_eq!(status, 403);
        assert_eq!(other["reason_code"], "TASK_TOKEN_MISMATCH");
        assert_eq!(
            fs::read_to_string(f.directory.join("workspace/src/main.rs")).unwrap(),
            initial
        );
        let manifest: Value =
            serde_json::from_slice(&fs::read(f.directory.join("experiment.json")).unwrap())
                .unwrap();
        assert_eq!(manifest["mode"], mode.name());
        assert_eq!(manifest["synthetic_fixture"], true);
    }
}

#[tokio::test]
async fn only_baseline_writes_without_phase_or_lease_and_does_not_consume_grants() {
    for mode in [Mode::Enforced, Mode::Baseline] {
        let f = Fixture::new(mode).await;
        let (status, first) = f
            .tool("write_file", "src/main.rs", Some("inspect write"), None)
            .await;
        if mode == Mode::Enforced {
            assert_eq!(status, 403);
            assert_eq!(first["reason_code"], "INVALID_STAGE");
        } else {
            assert_eq!(status, 200);
            assert_eq!(
                first["data"],
                json!({"bytes_written":13,"grant_id":null,"remaining_uses":null})
            );
        }
        assert_eq!(f.admin("/phase", json!({"phase":"edit"})).await.0, 200);
        let (status, grant) = f
            .admin(
                "/grants",
                json!({"tool":"write_file","path":"src/main.rs","ttl_seconds":60,"max_uses":1}),
            )
            .await;
        assert_eq!(status, 201);
        let gid = grant["grant_id"].as_str().unwrap();
        let (status, write) = f
            .tool("write_file", "src/main.rs", Some("paired write"), Some(gid))
            .await;
        assert_eq!(status, 200, "{write}");
        assert_eq!(write["executed"], true);
        assert_eq!(
            fs::read_to_string(f.directory.join("workspace/src/main.rs")).unwrap(),
            "paired write"
        );
        let (status, repeated) = f
            .tool("write_file", "src/main.rs", Some("repeat"), Some(gid))
            .await;
        assert_eq!(status, if mode == Mode::Enforced { 403 } else { 200 });
        if mode == Mode::Enforced {
            assert_eq!(repeated["reason_code"], "LEASE_USED_UP");
        }
        let detail_path = format!("/v1/admin/tasks/{}", f.task["task_id"].as_str().unwrap());
        let (_, detail) = send(&f.router, "GET", &detail_path, ADMIN, Value::Null).await;
        assert_eq!(
            detail["grants"][0]["remaining_uses"],
            if mode == Mode::Enforced { 0 } else { 1 }
        );
        assert_eq!(f.admin("/phase", json!({"phase":"verify"})).await.0, 200);
        assert_eq!(f.admin("/phase", json!({"phase":"done"})).await.0, 200);
        let (status, _) = f
            .tool("write_file", "src/main.rs", Some("done write"), None)
            .await;
        assert_eq!(status, if mode == Mode::Enforced { 403 } else { 200 });
    }
}

#[test]
fn experiment_cannot_reuse_a_directory_or_accept_unknown_modes() {
    let temp = tempfile::tempdir().unwrap();
    let sentinel = temp.path().join("sentinel");
    fs::write(&sentinel, "keep").unwrap();
    assert!(experiment::prepare(temp.path(), Mode::Baseline, ADMIN).is_err());
    assert_eq!(fs::read_to_string(sentinel).unwrap(), "keep");
    assert!("disabled".parse::<Mode>().is_err());
}

#[tokio::test]
async fn product_constructor_still_enforces_when_bench_feature_is_compiled() {
    let f = Fixture::new(Mode::Baseline).await;
    let config = Configuration::load(&f.directory.join("policy.json")).unwrap();
    let product =
        app(Broker::open(config, ADMIN, &f.directory.join("product-audit.jsonl")).unwrap());
    let (status, task) = send(
        &product,
        "POST",
        "/v1/admin/tasks",
        ADMIN,
        json!({"workspace_id":"demo"}),
    )
    .await;
    assert_eq!(status, 201);
    let (status, denied) = send(&product,"POST","/v1/tool/execute",task["agent_token"].as_str().unwrap(),json!({"task_id":task["task_id"],"tool":"write_file","path":"src/main.rs","content":"forbidden"})).await;
    assert_eq!(status, 403);
    assert_eq!(denied["reason_code"], "INVALID_STAGE");
}
