//! Paired HTTP-vs-MCP read timing over the same broker, fixture and audit path.
//!
//! This is a local end-to-end observation, not an isolated protocol benchmark
//! or a statistical guarantee. Failures abort the run; no slow samples are dropped.

use agentfence_server::{Broker, Configuration, app};
use reqwest::Client;
use rmcp::{ServiceExt, model::CallToolRequestParams, transport::TokioChildProcess};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    error::Error,
    fs,
    path::{Path, PathBuf},
    time::{Instant, SystemTime, UNIX_EPOCH},
};
use tokio::process::Command;

type Result<T> = std::result::Result<T, Box<dyn Error>>;
const WARMUP: usize = 60;
const ROUNDS: usize = 3;
const ITERATIONS: usize = 100;

#[derive(Serialize)]
struct Sample {
    kind: &'static str,
    round: usize,
    index: usize,
    order: usize,
    mode: &'static str,
    elapsed_ns: u64,
    request_id: String,
    sha256: String,
    bytes: usize,
}

fn random_hex() -> Result<String> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|_| "system random source unavailable")?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn call_args(path: &str) -> CallToolRequestParams {
    CallToolRequestParams::new("agentfence_read_file")
        .with_arguments(json!({"path":path}).as_object().expect("object").clone())
}
async fn post(http: &Client, url: &str, admin: &str, path: &str, body: Value) -> Result<Value> {
    let response = http
        .post(format!("{url}{path}"))
        .bearer_auth(admin)
        .json(&body)
        .send()
        .await?
        .error_for_status()?;
    Ok(response.json().await?)
}
fn percentile(values: &mut [u64], numerator: usize, denominator: usize) -> u64 {
    values.sort_unstable();
    let rank = (values.len() * numerator).div_ceil(denominator);
    values[rank.saturating_sub(1)]
}
fn stats(samples: &[Sample], mode: &str, round: Option<usize>) -> Value {
    let mut values: Vec<_> = samples
        .iter()
        .filter(|s| s.kind == "measured" && s.mode == mode && round.is_none_or(|r| s.round == r))
        .map(|s| s.elapsed_ns)
        .collect();
    let count = values.len();
    let p50 = percentile(&mut values, 50, 100);
    let p95 = percentile(&mut values, 95, 100);
    let p99 = percentile(&mut values, 99, 100);
    json!({"count":count,"p50_ns":p50,"p95_ns":p95,"p99_ns":p99})
}

#[tokio::main]
async fn main() -> Result<()> {
    let parameters: Vec<_> = std::env::args_os().skip(1).collect();
    let mcp = parameters
        .first()
        .map(PathBuf::from)
        .ok_or("usage: mcp_overhead <MCP executable> [new evidence directory]")?
        .canonicalize()?;
    let repo = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .ok_or("repository unavailable")?;
    let seconds = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let nonce = random_hex()?;
    let root = parameters.get(1).map(PathBuf::from).unwrap_or_else(|| {
        repo.join("results")
            .join(format!("local-v2-mcp-bench-{seconds}-{}", &nonce[..8]))
    });
    if let Some(parent) = root.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::create_dir(&root)?;
    let source = root.join("project");
    fs::create_dir(&source)?;
    let mut content = "pub fn fixture_value() -> u32 { 42 }\n".repeat(120);
    content.truncate(4096);
    while content.len() < 4096 {
        content.push(' ');
    }
    fs::write(source.join("benchmark.rs"), &content)?;
    let admin = random_hex()?;
    let broker = Broker::open(Configuration::empty(), &admin, &root.join("audit.jsonl"))?;
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
    let url = format!("http://{}", listener.local_addr()?);
    let server = tokio::spawn(async move { axum::serve(listener, app(broker)).await });
    let run = measure(&root, &source, &mcp, &url, &admin, &content).await;
    server.abort();
    let _ = server.await;
    let (samples, task_id, snapshot_id) = run?;
    let revision = std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(repo)
        .output()?;
    let status = std::process::Command::new("git")
        .args(["status", "--porcelain"])
        .current_dir(repo)
        .output()?;
    if !revision.status.success() || !status.status.success() {
        return Err("git provenance unavailable".into());
    }
    let rustc = std::process::Command::new("rustc")
        .arg("--version")
        .current_dir(repo)
        .output()?;
    if !rustc.status.success() {
        return Err("Rust toolchain version unavailable".into());
    }
    let mut rounds = Vec::new();
    for round in 1..=ROUNDS {
        rounds.push(json!({"round":round,"direct_http":stats(&samples,"direct_http",Some(round)),"mcp_stdio":stats(&samples,"mcp_stdio",Some(round))}));
    }
    let summary = json!({"kind":"paired_mcp_read_overhead","status":"passed","source_commit":String::from_utf8_lossy(&revision.stdout).trim(),"dirty_tree":!status.stdout.is_empty(),"os":std::env::consts::OS,"arch":std::env::consts::ARCH,"rustc":String::from_utf8_lossy(&rustc.stdout).trim(),"rust_version_requirement":env!("CARGO_PKG_RUST_VERSION"),"rmcp_version":"3.8.0","benchmark_binary_sha256":digest(&fs::read(std::env::current_exe()?)?),"mcp_binary_sha256":digest(&fs::read(mcp)?),"task_id":task_id,"snapshot_id":snapshot_id,"fixture_bytes":content.len(),"fixture_sha256":digest(content.as_bytes()),"concurrency":1,"rounds":ROUNDS,"samples_per_mode_per_round":ITERATIONS,"warmups_per_mode":WARMUP,"samples_file":"samples.jsonl","audit_file":"audit.jsonl","events_file":"events.json","api_disk_audit_equal":true,"aggregate":{"direct_http":stats(&samples,"direct_http",None),"mcp_stdio":stats(&samples,"mcp_stdio",None)},"per_round":rounds,"limitations":"One host, one 4096-byte synthetic read, persistent HTTP connections. The direct client and broker run in the benchmark process; MCP runs in its own process. Includes JSON-RPC, numbered output and adapter overhead. No LLM/network/CPU/RSS or significance claim."});
    let sample_lines = samples
        .iter()
        .map(serde_json::to_string)
        .collect::<std::result::Result<Vec<_>, _>>()?
        .join("\n")
        + "\n";
    let summary = serde_json::to_string_pretty(&summary)?;
    if sample_lines.contains(&admin) || summary.contains(&admin) {
        return Err("admin credential appeared in results".into());
    }
    fs::write(root.join("samples.jsonl"), sample_lines)?;
    fs::write(root.join("summary.json"), summary)?;
    println!(
        "{}",
        json!({"event":"V2_MCP_BENCH_PASS","evidence_dir":root,"measured_per_mode":ROUNDS*ITERATIONS})
    );
    Ok(())
}

async fn measure(
    root: &Path,
    source: &Path,
    mcp: &Path,
    url: &str,
    admin: &str,
    content: &str,
) -> Result<(Vec<Sample>, String, String)> {
    let http = Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(std::time::Duration::from_secs(2))
        .timeout(std::time::Duration::from_secs(10))
        .build()?;
    let preview = post(
        &http,
        url,
        admin,
        "/v1/admin/workspace-previews",
        json!({"source_path":source.canonicalize()?}),
    )
    .await?;
    let workspace=post(&http,url,admin,"/v1/admin/workspaces",json!({"preview_id":preview["preview_id"],"display_name":"benchmark fixture","profile":"readonly_snapshot"})).await?;
    let task=post(&http,url,admin,"/v1/admin/tasks",json!({"workspace_id":workspace["workspace_id"],"permission_profile":"readonly","goal":"Measure paired file reads"})).await?;
    let token = task["agent_token"].as_str().ok_or("missing agent token")?;
    let id = task["task_id"].as_str().ok_or("missing task id")?;
    let snapshot = workspace["snapshot_id"]
        .as_str()
        .ok_or("missing snapshot id")?;
    let expected = digest(content.as_bytes());
    let mut command = Command::new(mcp);
    command
        .env_clear()
        .env("AGENTFENCE_BROKER_URL", url)
        .env("AGENTFENCE_TASK_ID", id)
        .env("AGENTFENCE_AGENT_TOKEN", token)
        .kill_on_drop(true);
    #[cfg(windows)]
    if let Some(system) = std::env::var_os("SystemRoot") {
        command.env("SystemRoot", system);
    }
    let client = ().serve(TokioChildProcess::new(command)?).await?;
    if client.list_tools(None).await?.tools.len() != 3 {
        return Err("unexpected tool catalog".into());
    }
    let mut samples = Vec::with_capacity(2 * (WARMUP + ROUNDS * ITERATIONS));
    for pair in 0..WARMUP + ROUNDS * ITERATIONS {
        let kind = if pair < WARMUP { "warmup" } else { "measured" };
        let (round, index) = if pair < WARMUP {
            (0, pair + 1)
        } else {
            let i = pair - WARMUP;
            (i / ITERATIONS + 1, i % ITERATIONS + 1)
        };
        let order = if pair % 2 == 0 {
            ["direct_http", "mcp_stdio"]
        } else {
            ["mcp_stdio", "direct_http"]
        };
        for (position, mode) in order.into_iter().enumerate() {
            let start = Instant::now();
            let data = if mode == "direct_http" {
                let response = http
                    .post(format!("{url}/v1/tool/execute"))
                    .bearer_auth(token)
                    .json(&json!({"task_id":id,"tool":"read_file","path":"benchmark.rs"}))
                    .send()
                    .await?
                    .error_for_status()?;
                let body: Value = response.json().await?;
                if body["allowed"] != true || body["executed"] != true {
                    return Err("direct read was rejected".into());
                }
                json!({"request_id":body["request_id"],"sha256":body["data"]["sha256"],"content":body["data"]["content"],"bytes_read":body["data"]["bytes_read"],"snapshot_id":body["data"]["snapshot_id"]})
            } else {
                let response = client.call_tool(call_args("benchmark.rs")).await?;
                if response.is_error != Some(false) {
                    return Err("MCP read was rejected".into());
                }
                response.structured_content.ok_or("missing MCP result")?
            };
            let elapsed_ns = start.elapsed().as_nanos().min(u64::MAX as u128) as u64;
            if data["sha256"] != expected
                || data["content"] != content
                || data["bytes_read"] != content.len()
                || data["snapshot_id"] != snapshot
            {
                return Err("read response did not match fixture".into());
            }
            let request_id = data["request_id"]
                .as_str()
                .ok_or("missing request_id")?
                .to_owned();
            samples.push(Sample {
                kind,
                round,
                index,
                order: position + 1,
                mode,
                elapsed_ns,
                request_id,
                sha256: expected.clone(),
                bytes: content.len(),
            });
        }
    }
    client.cancel().await?;
    let mut api = Vec::new();
    let mut after: Option<String> = None;
    loop {
        let mut query = vec![("task_id", id.to_owned()), ("limit", "500".to_owned())];
        if let Some(cursor) = &after {
            query.push(("after", cursor.clone()));
        }
        let page: Value = http
            .get(format!("{url}/v1/admin/events"))
            .bearer_auth(admin)
            .query(&query)
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        api.extend(
            page["events"]
                .as_array()
                .ok_or("invalid events page")?
                .iter()
                .cloned(),
        );
        if page["has_more"] == false {
            break;
        }
        let next = page["next_cursor"]
            .as_str()
            .ok_or("missing cursor")?
            .to_owned();
        if after.as_ref() == Some(&next) {
            return Err("non-advancing cursor".into());
        }
        after = Some(next);
    }
    let disk: Vec<Value> = fs::read_to_string(root.join("audit.jsonl"))?
        .lines()
        .map(serde_json::from_str)
        .collect::<std::result::Result<_, _>>()?;
    let disk: Vec<_> = disk
        .into_iter()
        .filter(|event| event["task_id"] == id)
        .collect();
    if disk != api {
        return Err("API and disk audit differ".into());
    }
    let mut seen = std::collections::HashSet::new();
    for sample in &samples {
        if !seen.insert(&sample.request_id) {
            return Err("duplicate sample request_id".into());
        }
        let events: Vec<_> = api
            .iter()
            .filter(|event| event["request_id"] == sample.request_id)
            .collect();
        if events.len() != 2
            || events[0]["event_type"] != "intent"
            || events[0]["reason_code"] != "PENDING"
            || events[0]["executed"] != false
            || events[1]["event_type"] != "result"
            || events[1]["reason_code"] != "OK"
            || events[1]["executed"] != true
            || events[1]["allowed"] != true
            || events[1]["tool"] != "read_file"
            || events[1]["path"] != "benchmark.rs"
            || events[1]["snapshot_id"] != snapshot
        {
            return Err("sample audit evidence missing".into());
        }
    }
    if samples
        .iter()
        .filter(|s| s.kind == "measured" && s.mode == "direct_http")
        .count()
        != ROUNDS * ITERATIONS
        || samples
            .iter()
            .filter(|s| s.kind == "measured" && s.mode == "mcp_stdio")
            .count()
            != ROUNDS * ITERATIONS
    {
        return Err("unexpected sample count".into());
    }
    let events = serde_json::to_string_pretty(&api)?;
    if events.contains(token)
        || events.contains(admin)
        || serde_json::to_string(&samples)?.contains(token)
    {
        return Err("credential appeared in audit evidence".into());
    }
    fs::write(root.join("events.json"), events)?;
    Ok((samples, id.into(), snapshot.into()))
}
