//! Profile invariants are enforced through the same HTTP router as legacy tools.

use agentfence_server::{Broker, Configuration, app};
use axum::{Router, body::Body, http::Request};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use std::fs;
use tower::ServiceExt;

const ADMIN: &str = "readonly-test-only-admin-never-use-in-production";

struct Fixture {
    root: tempfile::TempDir,
    router: Router,
}
impl Fixture {
    fn new() -> Self {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("project")).unwrap();
        fs::write(root.path().join("project/main.rs"), "fn main() {}\n").unwrap();
        let config = Configuration::from_json(
            &json!({"workspaces":{"demo":{"root":"project","allowed_paths":["main.rs"]}}})
                .to_string(),
            root.path(),
        )
        .unwrap();
        let broker = Broker::open(config, ADMIN, &root.path().join("audit.jsonl")).unwrap();
        Self {
            root,
            router: app(broker),
        }
    }
    async fn call(&self, method: &str, path: &str, token: &str, body: Value) -> (u16, Value) {
        let response = self
            .router
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
        (
            status,
            serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes())
                .unwrap(),
        )
    }
    async fn task(&self) -> Value {
        let (status, preview) = self
            .call(
                "POST",
                "/v1/admin/workspace-previews",
                ADMIN,
                json!({"source_path":self.root.path().join("project")}),
            )
            .await;
        assert_eq!(status, 201, "{preview}");
        let (status,workspace)=self.call("POST","/v1/admin/workspaces",ADMIN,json!({"preview_id":preview["preview_id"],"display_name":"Review fixture","profile":"readonly_snapshot"})).await;
        assert_eq!(status, 201, "{workspace}");
        let (status, body) = self
            .call(
                "POST",
                "/v1/admin/tasks",
                ADMIN,
                json!({"workspace_id":workspace["workspace_id"],"permission_profile":"readonly","goal":"Review code"}),
            )
            .await;
        assert_eq!(status, 201, "{body}");
        body
    }
}

#[tokio::test]
async fn readonly_denies_write_grants_and_edit_with_task_audit() {
    let f = Fixture::new();
    let task = f.task().await;
    let id = task["task_id"].as_str().unwrap();
    let token = task["agent_token"].as_str().unwrap();
    for (route, auth, body) in [
        (
            format!("/v1/admin/tasks/{id}/phase"),
            ADMIN,
            json!({"phase":"edit"}),
        ),
        (
            format!("/v1/admin/tasks/{id}/grants"),
            ADMIN,
            json!({"tool":"write_file","path":"main.rs","ttl_seconds":60,"max_uses":1}),
        ),
        (
            "/v1/tool/execute".into(),
            token,
            json!({"task_id":id,"tool":"write_file","path":"main.rs","content":"changed"}),
        ),
        (
            "/v1/tool/execute".into(),
            token,
            json!({"task_id":id,"tool":"write_file","path":"main.rs","content":"changed","grant_id":"forged"}),
        ),
    ] {
        let (status, body) = f.call("POST", &route, auth, body).await;
        assert_eq!(status, 403, "{body}");
        assert_eq!(body["reason_code"], "READONLY_TASK");
        let audit = fs::read_to_string(f.root.path().join("audit.jsonl")).unwrap();
        let event: Value = serde_json::from_str(audit.lines().last().unwrap()).unwrap();
        assert_eq!(event["task_id"], id);
        assert_eq!(event["request_id"], body["request_id"]);
        assert_eq!(event["allowed"], false);
        assert_eq!(event["executed"], false);
    }
    assert_eq!(
        fs::read_to_string(f.root.path().join("project/main.rs")).unwrap(),
        "fn main() {}\n"
    );
}

#[tokio::test]
async fn readonly_advances_without_edit_and_done_blocks_reads() {
    let f = Fixture::new();
    let task = f.task().await;
    let id = task["task_id"].as_str().unwrap();
    let token = task["agent_token"].as_str().unwrap();
    let read = json!({"task_id":id,"tool":"read_file","path":"main.rs"});
    for phase in ["inspect", "verify"] {
        if phase == "verify" {
            assert_eq!(
                f.call(
                    "POST",
                    &format!("/v1/admin/tasks/{id}/phase"),
                    ADMIN,
                    json!({"phase":"verify"})
                )
                .await
                .0,
                200
            );
        }
        let (status, body) = f
            .call("POST", "/v1/tool/execute", token, read.clone())
            .await;
        assert_eq!(status, 200);
        assert_eq!(body["data"]["sha256"].as_str().unwrap().len(), 64);
    }
    assert_eq!(
        f.call(
            "POST",
            &format!("/v1/admin/tasks/{id}/phase"),
            ADMIN,
            json!({"phase":"done"})
        )
        .await
        .0,
        200
    );
    assert_eq!(
        f.call("POST", "/v1/tool/execute", token, read).await.1["reason_code"],
        "INVALID_STAGE"
    );
}

#[tokio::test]
async fn readonly_cannot_change_its_profile_or_use_another_task_identity() {
    let f = Fixture::new();
    let a = f.task().await;
    let b = f.task().await;
    let token = a["agent_token"].as_str().unwrap();
    assert_eq!(
        f.call(
            "POST",
            "/v1/tool/execute",
            token,
            json!({"task_id":b["task_id"],"tool":"read_file","path":"main.rs"})
        )
        .await
        .1["reason_code"],
        "TASK_TOKEN_MISMATCH"
    );
    assert_eq!(
        f.call(
            "POST",
            "/v1/admin/tasks",
            token,
            json!({"workspace_id":"demo","permission_profile":"controlled_edit"})
        )
        .await
        .1["reason_code"],
        "ROLE_DENIED"
    );
    assert_eq!(f.call("POST","/v1/tool/execute",token,json!({"task_id":a["task_id"],"tool":"read_file","path":"main.rs","permission_profile":"controlled_edit"})).await.0,400);
}
