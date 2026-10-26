//! Consumer-facing health and audit contracts, exercised through the real router.

use agentfence_server::{Broker, Configuration, app};
use axum::{Router, body::Body, http::Request};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use std::fs;
use tower::ServiceExt;

const ADMIN: &str = "contract-test-admin-credential-never-use-in-production";
const CONTENT: &str = "contract fixture\n";

struct Fixture {
    directory: tempfile::TempDir,
    router: Router,
}

impl Fixture {
    fn new() -> Self {
        let directory = tempfile::tempdir().unwrap();
        fs::create_dir(directory.path().join("workspace")).unwrap();
        fs::write(directory.path().join("workspace/file.txt"), CONTENT).unwrap();
        let config = Configuration::from_json(
            &json!({"workspaces":{"demo":{
                "root":"workspace", "allowed_paths":["file.txt"]
            }}})
            .to_string(),
            directory.path(),
        )
        .unwrap();
        let broker = Broker::open(config, ADMIN, &directory.path().join("audit.jsonl")).unwrap();
        Self {
            directory,
            router: app(broker),
        }
    }

    async fn request(
        &self,
        method: &str,
        path: &str,
        token: Option<&str>,
        body: Option<Value>,
    ) -> (u16, Value) {
        let mut request = Request::builder()
            .method(method)
            .uri(path)
            .header("content-type", "application/json");
        if let Some(token) = token {
            request = request.header("authorization", format!("Bearer {token}"));
        }
        let response = self
            .router
            .clone()
            .oneshot(
                request
                    .body(Body::from(body.map(|v| v.to_string()).unwrap_or_default()))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status().as_u16();
        let bytes = response.into_body().collect().await.unwrap().to_bytes();
        (status, serde_json::from_slice(&bytes).unwrap())
    }
}

#[tokio::test]
async fn healthy_response_has_status_and_instance_without_audit_available() {
    let fixture = Fixture::new();
    let (status, health) = fixture.request("GET", "/healthz", None, None).await;
    assert_eq!(status, 200);
    let instance = health["instance_id"].as_str().unwrap();
    assert!(instance.starts_with("inst_"));
    assert_eq!(health, json!({"status":"ok", "instance_id":instance}));
}

#[tokio::test]
async fn audit_queries_match_jsonl_and_use_intent_result_and_numeric_ids() {
    let fixture = Fixture::new();
    let (status, task) = fixture
        .request(
            "POST",
            "/v1/admin/tasks",
            Some(ADMIN),
            Some(json!({"workspace_id":"demo"})),
        )
        .await;
    assert_eq!(status, 201);
    let task_id = task["task_id"].as_str().unwrap();
    let token = task["agent_token"].as_str().unwrap();
    let instance = task["instance_id"].as_str().unwrap();
    let (status, read) = fixture
        .request(
            "POST",
            "/v1/tool/execute",
            Some(token),
            Some(json!({"task_id":task_id, "tool":"read_file", "path":"file.txt"})),
        )
        .await;
    assert_eq!(status, 200);
    assert_eq!(read["data"]["content"], CONTENT);

    let (status, page) = fixture
        .request("GET", "/v1/admin/events?after=0", Some(ADMIN), None)
        .await;
    assert_eq!(status, 200);
    assert_eq!(page["instance_id"], instance);
    assert_eq!(page["has_more"], false);
    let events = page["events"].as_array().unwrap();
    assert_eq!(events.len(), 3);
    let mut previous_id = 0;
    for event in events {
        let id = event["event_id"]
            .as_u64()
            .expect("event_id must be numeric");
        assert!(id > previous_id);
        assert_eq!(event["instance_id"], instance);
        assert_eq!(event["task_id"], task_id);
        previous_id = id;
    }
    assert_eq!(events[0]["event_type"], "task_created");
    assert_eq!(events[1]["event_type"], "intent");
    assert_eq!(events[1]["request_id"], read["request_id"]);
    assert_eq!(events[1]["reason_code"], "PENDING");
    assert_eq!(events[1]["allowed"], false);
    assert_eq!(events[1]["executed"], false);
    assert_eq!(events[2]["event_type"], "result");
    assert_eq!(events[2]["request_id"], read["request_id"]);
    assert_eq!(events[2]["reason_code"], "OK");
    assert_eq!(events[2]["allowed"], true);
    assert_eq!(events[2]["executed"], true);

    let lines = fs::read_to_string(fixture.directory.path().join("audit.jsonl")).unwrap();
    let persisted: Vec<Value> = lines
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(&persisted, events);

    let cursor = page["next_cursor"].as_str().unwrap();
    assert_eq!(cursor, format!("{instance}:{previous_id}"));
    // Consumers pass the cursor back intact; it is not a numeric event ID.
    let (status, next) = fixture
        .request(
            "GET",
            &format!("/v1/admin/events?after={cursor}"),
            Some(ADMIN),
            None,
        )
        .await;
    assert_eq!(status, 200);
    assert_eq!(next["events"], json!([]));
    assert_eq!(next["next_cursor"], cursor);
}
