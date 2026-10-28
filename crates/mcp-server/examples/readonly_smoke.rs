//! Offline protocol replay with generated projects, real MCP stdio and JSONL.

use agentfence_server::{Broker, Configuration, app};
use reqwest::Client;
use rmcp::{ServiceExt, model::CallToolRequestParams, transport::TokioChildProcess};
use serde_json::{Value, json};
use std::{
    error::Error,
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::process::Command;

type Result<T> = std::result::Result<T, Box<dyn Error>>;
fn random_hex() -> Result<String> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|_| "system random source unavailable")?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}
fn args(name: &'static str, value: Value) -> CallToolRequestParams {
    CallToolRequestParams::new(name)
        .with_arguments(value.as_object().expect("static object").clone())
}
async fn post(
    http: &Client,
    url: &str,
    admin: &str,
    path: &str,
    value: Value,
    expected: u16,
) -> Result<Value> {
    let response = http
        .post(format!("{url}{path}"))
        .bearer_auth(admin)
        .json(&value)
        .send()
        .await?;
    let status = response.status().as_u16();
    let value: Value = response.json().await?;
    if status != expected {
        return Err(format!("unexpected HTTP {status}; expected {expected}").into());
    }
    Ok(value)
}

#[tokio::main]
async fn main() -> Result<()> {
    let parameters: Vec<_> = std::env::args_os().skip(1).collect();
    let binary = parameters
        .first()
        .map(PathBuf::from)
        .ok_or("usage: readonly_smoke <MCP executable> [new evidence directory]")?
        .canonicalize()?;
    let repo = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .ok_or("repository unavailable")?;
    let seconds = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let run_id = random_hex()?;
    let root = parameters.get(1).map(PathBuf::from).unwrap_or_else(|| {
        repo.join("results")
            .join(format!("local-v2-{seconds}-{}", &run_id[..8]))
    });
    if let Some(parent) = root.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::create_dir(&root)?; // Refuse to overwrite previous evidence.
    let admin = random_hex()?;
    let broker = Broker::open(Configuration::empty(), &admin, &root.join("audit.jsonl"))?;
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
    let url = format!("http://{}", listener.local_addr()?);
    let server = tokio::spawn(async move { axum::serve(listener, app(broker)).await });
    let result = replay(&root, &binary, &url, &admin).await;
    server.abort();
    let _ = server.await;
    let runs = result?;
    let commit = std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(repo)
        .output()?;
    let dirty = std::process::Command::new("git")
        .args(["status", "--porcelain"])
        .current_dir(repo)
        .output()?;
    let evidence = json!({"kind":"deterministic_mcp_replay","passed":true,"model_used":false,"rmcp_version":"3.8.0","source_commit":String::from_utf8_lossy(&commit.stdout).trim(),"dirty_tree":!dirty.stdout.is_empty(),"os":std::env::consts::OS,"arch":std::env::consts::ARCH,"runs":runs,"audit_file":"audit.jsonl","scope":"Generated fixtures; official rmcp client and real MCP subprocess; no LLM review, OS isolation or performance claim"});
    let serialized = serde_json::to_string_pretty(&evidence)?;
    if serialized.contains(&admin) {
        return Err("credential appeared in replay evidence".into());
    }
    fs::write(root.join("evidence.json"), serialized)?;
    println!(
        "{}",
        json!({"event":"V2_MCP_REPLAY_PASS","projects":2,"evidence_dir":root,"model_used":false})
    );
    Ok(())
}

async fn replay(root: &Path, binary: &Path, url: &str, admin: &str) -> Result<Vec<Value>> {
    let http = Client::builder().no_proxy().build()?;
    let mut runs = Vec::new();
    for (name, files) in [
        (
            "project-a",
            vec![
                ("src/main.rs", "fn main() {}\n"),
                ("src/lib.rs", "pub fn value()->u8 {42}\n"),
                ("Cargo.toml", "[package]\nname='fixture-a'\n"),
            ],
        ),
        (
            "project-b",
            vec![
                ("packages/worker.rs", "pub struct Worker;\n"),
                ("config.yaml", "enabled: true\n"),
                ("README.md", "# Fixture B\n"),
            ],
        ),
    ] {
        let source = root.join(name);
        fs::create_dir(&source)?;
        for (path, text) in &files {
            let path = source.join(path);
            fs::create_dir_all(path.parent().ok_or("invalid fixture path")?)?;
            fs::write(path, text)?;
        }
        fs::write(source.join(".env"), "SIMULATED_PRIVATE_FIXTURE")?;
        let preview = post(
            &http,
            url,
            admin,
            "/v1/admin/workspace-previews",
            json!({"source_path":source.canonicalize()?}),
            201,
        )
        .await?;
        let workspace=post(&http,url,admin,"/v1/admin/workspaces",json!({"preview_id":preview["preview_id"],"display_name":name,"profile":"readonly_snapshot"}),201).await?;
        let mut task=post(&http,url,admin,"/v1/admin/tasks",json!({"workspace_id":workspace["workspace_id"],"permission_profile":"readonly","goal":"Review the generated fixture"}),201).await?;
        let token = task
            .as_object_mut()
            .and_then(|o| o.remove("agent_token"))
            .and_then(|v| v.as_str().map(str::to_owned))
            .ok_or("missing task token")?;
        let id = task["task_id"].as_str().ok_or("missing task id")?;
        let mut command = Command::new(binary);
        command
            .env_clear()
            .env("AGENTFENCE_BROKER_URL", url)
            .env("AGENTFENCE_TASK_ID", id)
            .env("AGENTFENCE_AGENT_TOKEN", &token)
            .kill_on_drop(true);
        #[cfg(windows)]
        if let Some(system) = std::env::var_os("SystemRoot") {
            command.env("SystemRoot", system);
        }
        let client = ().serve(TokioChildProcess::new(command)?).await?;
        let catalog = client.list_tools(None).await?;
        if catalog.tools.len() != 3 {
            return Err("unexpected tool catalog".into());
        }
        let list = client
            .call_tool(args("agentfence_list_files", json!({})))
            .await?
            .structured_content
            .ok_or("missing file list")?;
        let mut calls = Vec::new();
        for file in workspace["files"].as_array().ok_or("missing manifest")? {
            let result = client
                .call_tool(args("agentfence_read_file", json!({"path":file["path"]})))
                .await?;
            let data = result.structured_content.ok_or("missing read evidence")?;
            if result.is_error != Some(false) || data["sha256"] != file["sha256"] {
                return Err("snapshot hash mismatch".into());
            }
            calls.push(json!({"tool":"agentfence_read_file","path":file["path"],"sha256":data["sha256"],"request_id":data["request_id"],"allowed":true}));
        }
        for path in ["../outside.txt", ".env"] {
            let result = client
                .call_tool(args("agentfence_read_file", json!({"path":path})))
                .await?;
            let data = result.structured_content.ok_or("missing denial evidence")?;
            if result.is_error != Some(true) || data["reason_code"] != "PATH_DENIED" {
                return Err("expected path denial".into());
            }
            calls.push(json!({"tool":"agentfence_read_file","path":path,"reason_code":data["reason_code"],"request_id":data["request_id"],"allowed":false}));
        }
        for (path, body) in [
            (
                format!("/v1/admin/tasks/{id}/phase"),
                json!({"phase":"edit"}),
            ),
            (
                format!("/v1/admin/tasks/{id}/grants"),
                json!({"tool":"write_file","path":files[0].0,"ttl_seconds":60,"max_uses":1}),
            ),
        ] {
            let data = post(&http, url, admin, &path, body, 403).await?;
            if data["reason_code"] != "READONLY_TASK" {
                return Err("expected immutable profile denial".into());
            }
            calls.push(json!({"route":path,"reason_code":data["reason_code"],"request_id":data["request_id"],"allowed":false}));
        }
        let response=http.post(format!("{url}/v1/tool/execute")).bearer_auth(&token).json(&json!({"task_id":id,"tool":"write_file","path":files[0].0,"content":"forged","grant_id":"forged"})).send().await?;
        if response.status().as_u16() != 403 {
            return Err("direct write was not blocked".into());
        }
        let denial: Value = response.json().await?;
        if denial["reason_code"] != "READONLY_TASK" {
            return Err("wrong readonly denial".into());
        }
        calls.push(json!({"tool":"write_file","reason_code":denial["reason_code"],"request_id":denial["request_id"],"allowed":false}));
        post(
            &http,
            url,
            admin,
            &format!("/v1/admin/tasks/{id}/phase"),
            json!({"phase":"verify"}),
            200,
        )
        .await?;
        let verified = client
            .call_tool(args("agentfence_read_file", json!({"path":files[0].0})))
            .await?;
        if verified.is_error != Some(false) {
            return Err("verification read failed".into());
        }
        post(
            &http,
            url,
            admin,
            &format!("/v1/admin/tasks/{id}/phase"),
            json!({"phase":"done"}),
            200,
        )
        .await?;
        client.cancel().await?;
        let events: Vec<Value> = fs::read_to_string(root.join("audit.jsonl"))?
            .lines()
            .map(serde_json::from_str)
            .collect::<std::result::Result<_, _>>()?;
        let exported: Value = http
            .get(format!("{url}/v1/admin/events"))
            .bearer_auth(admin)
            .query(&[("task_id", id), ("limit", "500")])
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        let task_events: Vec<_> = events.iter().filter(|e| e["task_id"] == id).collect();
        if exported["has_more"] != false
            || serde_json::to_value(&task_events)? != exported["events"]
        {
            return Err("API and disk audit differ".into());
        }
        fs::write(
            root.join(format!("events-{name}.json")),
            serde_json::to_string_pretty(&exported)?,
        )?;
        for call in &calls {
            if !events.iter().any(|event| {
                event["request_id"] == call["request_id"]
                    && event["task_id"] == id
                    && event["event_type"] == "result"
            }) {
                return Err("missing correlated audit result".into());
            }
        }
        for (path, text) in files {
            if fs::read_to_string(source.join(path))? != text {
                return Err("source fixture was modified".into());
            }
        }
        let result = json!({"project":name,"task":task,"snapshot_id":workspace["snapshot_id"],"manifest_sha256":workspace["manifest_sha256"],"files":workspace["files"],"listed_files":list["total"],"calls":calls,"verified_read":verified.structured_content.map(|mut value|{if let Some(map)=value.as_object_mut(){map.remove("content");map.remove("numbered_content");}value}),"source_unchanged":true});
        if result.to_string().contains(&token) {
            return Err("task token appeared in replay evidence".into());
        }
        runs.push(result);
    }
    Ok(runs)
}
