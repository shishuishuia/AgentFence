//! Official rmcp clients exercise a separate MCP executable over real stdio.

use agentfence_mcp_server::McpServer;
use agentfence_server::{Broker, Configuration, app};
use reqwest::Client;
use rmcp::{ServiceExt, model::CallToolRequestParams, transport::TokioChildProcess};
use serde_json::{Value, json};
use std::{fs, process::Stdio};
use tokio::{process::Command, task::JoinHandle};

const ADMIN: &str = "mcp-integration-test-admin-not-for-production-00000000";
struct Fixture {
    root: tempfile::TempDir,
    url: String,
    server: JoinHandle<()>,
    http: Client,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.server.abort();
    }
}
impl Fixture {
    async fn new() -> Self {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir(root.path().join("legacy")).unwrap();
        fs::write(root.path().join("legacy/main.rs"), "legacy").unwrap();
        let config = Configuration::from_json(
            &json!({"workspaces":{"legacy":{"root":"legacy","allowed_paths":["main.rs"]}}})
                .to_string(),
            root.path(),
        )
        .unwrap();
        let broker = Broker::open(config, ADMIN, &root.path().join("audit.jsonl")).unwrap();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move {
            axum::serve(listener, app(broker)).await.unwrap();
        });
        Self {
            root,
            url,
            server,
            http: Client::builder().no_proxy().build().unwrap(),
        }
    }
    async fn post(&self, path: &str, body: Value) -> Value {
        let response = self
            .http
            .post(format!("{}{path}", self.url))
            .bearer_auth(ADMIN)
            .json(&body)
            .send()
            .await
            .unwrap();
        let status = response.status();
        let body: Value = response.json().await.unwrap();
        assert!(status.is_success(), "{body}");
        body
    }
    async fn import(&self, name: &str) -> (Value, Value) {
        let source = self.root.path().join(name);
        fs::create_dir_all(source.join("src")).unwrap();
        fs::write(
            source.join("src/main.rs"),
            "fn main() {\n    println!(\"review fixture\");\n}\n",
        )
        .unwrap();
        fs::write(
            source.join("Cargo.toml"),
            "[package]\nname='review-fixture'\n",
        )
        .unwrap();
        fs::write(source.join("README.md"), "# Review fixture\n").unwrap();
        fs::write(source.join(".env"), "SIMULATED_PRIVATE_TEST_DATA").unwrap();
        let preview = self
            .post(
                "/v1/admin/workspace-previews",
                json!({"source_path":source}),
            )
            .await;
        let workspace=self.post("/v1/admin/workspaces",json!({"preview_id":preview["preview_id"],"display_name":name,"profile":"readonly_snapshot"})).await;
        let task=self.post("/v1/admin/tasks",json!({"workspace_id":workspace["workspace_id"],"permission_profile":"readonly","goal":"Review at least three files and cite source lines"})).await;
        (workspace, task)
    }
    fn command(&self, task: &Value) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_agentfence-mcp-server"));
        command
            .env_clear()
            .env("AGENTFENCE_BROKER_URL", &self.url)
            .env("AGENTFENCE_TASK_ID", task["task_id"].as_str().unwrap())
            .env(
                "AGENTFENCE_AGENT_TOKEN",
                task["agent_token"].as_str().unwrap(),
            )
            .kill_on_drop(true);
        #[cfg(windows)]
        if let Some(root) = std::env::var_os("SystemRoot") {
            command.env("SystemRoot", root);
        }
        command
    }
}

fn params(name: &'static str, args: Value) -> CallToolRequestParams {
    CallToolRequestParams::new(name).with_arguments(args.as_object().unwrap().clone())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn official_client_discovers_reads_reconnects_and_correlates_real_audit() {
    let f = Fixture::new().await;
    for project in ["rust-app", "nested-code-review"] {
        let (workspace, task) = f.import(project).await;
        let client = ().serve(TokioChildProcess::new(f.command(&task)).unwrap()).await.unwrap();
        let catalog = client.list_tools(None).await.unwrap();
        assert_eq!(catalog.tools.len(), 3);
        let names: Vec<_> = catalog.tools.iter().map(|t| t.name.as_ref()).collect();
        for name in [
            "agentfence_list_files",
            "agentfence_read_file",
            "agentfence_task_status",
        ] {
            assert!(names.contains(&name));
        }
        for tool in &catalog.tools {
            assert_eq!(tool.input_schema["additionalProperties"], false);
            assert_eq!(
                tool.annotations.as_ref().unwrap().read_only_hint,
                Some(true)
            );
        }
        let listing = client
            .call_tool(params("agentfence_list_files", json!({"limit":2})))
            .await
            .unwrap();
        let listing = listing.structured_content.unwrap();
        assert_eq!(listing["total"], 3);
        assert_eq!(listing["files"].as_array().unwrap().len(), 2);
        let next = client
            .call_tool(params(
                "agentfence_list_files",
                json!({"cursor":listing["next_cursor"]}),
            ))
            .await
            .unwrap();
        assert_eq!(
            next.structured_content.unwrap()["files"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        for file in workspace["files"].as_array().unwrap() {
            let result = client
                .call_tool(params("agentfence_read_file", json!({"path":file["path"]})))
                .await
                .unwrap();
            assert_eq!(result.is_error, Some(false));
            let data = result.structured_content.unwrap();
            assert_eq!(data["sha256"], file["sha256"]);
            assert_eq!(data["snapshot_id"], workspace["snapshot_id"]);
            assert!(
                data["numbered_content"]
                    .as_str()
                    .unwrap()
                    .starts_with("1: ")
            );
            let audit = fs::read_to_string(f.root.path().join("audit.jsonl")).unwrap();
            let events: Vec<Value> = audit
                .lines()
                .map(|s| serde_json::from_str(s).unwrap())
                .collect();
            assert!(events.iter().any(|e| e["request_id"] == data["request_id"]
                && e["task_id"] == task["task_id"]
                && e["event_type"] == "result"
                && e["executed"] == true));
            assert!(!data.to_string().contains(ADMIN));
            assert!(
                !data
                    .to_string()
                    .contains(task["agent_token"].as_str().unwrap())
            );
        }
        for path in ["../private.txt", ".env", "C:/private.txt", "missing.rs"] {
            let result = client
                .call_tool(params("agentfence_read_file", json!({"path":path})))
                .await
                .unwrap();
            assert_eq!(result.is_error, Some(true));
            let data = result.structured_content.unwrap();
            assert_eq!(data["reason_code"], "PATH_DENIED");
            assert!(data["request_id"].as_str().unwrap().starts_with("req_"));
        }
        for (name, args) in [
            (
                "write_file",
                json!({"path":"src/main.rs","content":"forged"}),
            ),
            (
                "agentfence_read_file",
                json!({"path":"src/main.rs","task_id":"forged"}),
            ),
            ("agentfence_task_status", json!({"workspace_root":"C:/"})),
            ("agentfence_read_file", json!({"path":4})),
        ] {
            assert!(client.call_tool(params(name, args)).await.is_err());
        }
        client.cancel().await.unwrap();
        let client = ().serve(TokioChildProcess::new(f.command(&task)).unwrap()).await.unwrap();
        let state = client
            .call_tool(params("agentfence_task_status", json!({})))
            .await
            .unwrap()
            .structured_content
            .unwrap();
        assert_eq!(state["permission_profile"], "readonly");
        assert!(!state.to_string().contains("source_path"));
        let id = task["task_id"].as_str().unwrap();
        f.post(
            &format!("/v1/admin/tasks/{id}/phase"),
            json!({"phase":"verify"}),
        )
        .await;
        assert_eq!(
            client
                .call_tool(params(
                    "agentfence_read_file",
                    json!({"path":"src/main.rs"})
                ))
                .await
                .unwrap()
                .is_error,
            Some(false)
        );
        f.post(
            &format!("/v1/admin/tasks/{id}/phase"),
            json!({"phase":"done"}),
        )
        .await;
        let denied = client
            .call_tool(params(
                "agentfence_read_file",
                json!({"path":"src/main.rs"}),
            ))
            .await
            .unwrap();
        assert_eq!(
            denied.structured_content.unwrap()["reason_code"],
            "INVALID_STAGE"
        );
        client.cancel().await.unwrap();
        assert!(
            fs::read_to_string(f.root.path().join(project).join("src/main.rs"))
                .unwrap()
                .contains("review fixture")
        );
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn startup_rejects_admin_wrong_task_editable_tasks_and_remote_endpoints() {
    let f = Fixture::new().await;
    let (_, task) = f.import("project").await;
    let id = task["task_id"].as_str().unwrap();
    let token = task["agent_token"].as_str().unwrap();
    assert!(McpServer::connect(&f.url, id, ADMIN.into()).await.is_err());
    assert!(
        McpServer::connect(&f.url, "task_another", token.into())
            .await
            .is_err()
    );
    let editable = f
        .post("/v1/admin/tasks", json!({"workspace_id":"legacy"}))
        .await;
    assert!(
        McpServer::connect(
            &f.url,
            editable["task_id"].as_str().unwrap(),
            editable["agent_token"].as_str().unwrap().into()
        )
        .await
        .is_err()
    );
    for url in [
        "https://127.0.0.1:8080",
        "http://example.com:8080",
        "http://127.0.0.1:8080/other",
        "http://user:pass@127.0.0.1:8080",
        "http://127.0.0.1:8080?key=forged",
    ] {
        assert!(McpServer::connect(url, id, token.into()).await.is_err());
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn stdio_process_refuses_inherited_admin_credentials_without_echoing_them() {
    let f = Fixture::new().await;
    let (_, task) = f.import("project").await;
    let output = f
        .command(&task)
        .env("AGENTFENCE_ADMIN_TOKEN", ADMIN)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .await
        .unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(!String::from_utf8_lossy(&output.stderr).contains(ADMIN));
}
