//! v2.1 conformance through the actual router, including faults and side effects.

use agentfence_core::{ReasonCode, TimePoint};
use agentfence_server::{
    Broker, Configuration, app,
    audit::AuditSink,
    clock::Clock,
    files::{FileOperations, LocalFiles},
};
use axum::{Router, body::Body, http::Request};
use chrono::{TimeZone, Utc};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use std::{
    fs, io,
    path::Path,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
    },
    time::Duration,
};
use tower::ServiceExt;

const ADMIN: &str = "test-only-admin-credential-never-use-in-production-00000000";
const INITIAL: &str = "fn main() { println!(\"before\"); }\n";

#[derive(Default)]
struct ManualClock {
    nanos: AtomicU64,
    utc_shift: AtomicU64,
}
impl Clock for ManualClock {
    fn sample(&self) -> TimePoint {
        let nanos = self.nanos.load(Ordering::SeqCst);
        TimePoint {
            monotonic: Duration::from_nanos(nanos),
            utc: Utc.timestamp_opt(1_700_000_000, 0).unwrap()
                + chrono::Duration::nanoseconds(nanos as i64)
                + chrono::Duration::seconds(self.utc_shift.load(Ordering::SeqCst) as i64),
        }
    }
}

#[derive(Default)]
struct SinkState {
    lines: Vec<Vec<u8>>,
    calls: usize,
    fail_at: Option<usize>,
}
struct MemorySink(Arc<Mutex<SinkState>>);
impl AuditSink for MemorySink {
    fn append(&mut self, bytes: &[u8]) -> io::Result<()> {
        let mut state = self.0.lock().unwrap();
        state.calls += 1;
        if state.fail_at == Some(state.calls) {
            return Err(io::Error::other("injected audit failure"));
        }
        state.lines.push(bytes.to_vec());
        Ok(())
    }
}

#[derive(Default)]
struct SpyFiles {
    reads: AtomicUsize,
    writes: AtomicUsize,
    fail_write: AtomicBool,
}
impl FileOperations for SpyFiles {
    fn read(&self, path: &Path, limit: usize) -> Result<String, ReasonCode> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        LocalFiles.read(path, limit)
    }
    fn write(&self, path: &Path, content: &str) -> Result<usize, ReasonCode> {
        self.writes.fetch_add(1, Ordering::SeqCst);
        if self.fail_write.load(Ordering::SeqCst) {
            return Err(ReasonCode::IoError);
        }
        LocalFiles.write(path, content)
    }
}

struct Fixture {
    temp: tempfile::TempDir,
    router: Router,
    config: Configuration,
    clock: Arc<ManualClock>,
    sink: Arc<Mutex<SinkState>>,
    files: Arc<SpyFiles>,
}
struct Session {
    id: String,
    token: String,
}

impl Fixture {
    fn new() -> Self {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("workspace");
        fs::create_dir_all(root.join("src")).unwrap();
        fs::create_dir(root.join("directory")).unwrap();
        fs::write(root.join("src/main.rs"), INITIAL).unwrap();
        fs::write(root.join("Cargo.toml"), "fixture").unwrap();
        fs::write(root.join("text.bin"), "text").unwrap();
        fs::write(temp.path().join("secret.txt"), "SIMULATED_PRIVATE_FIXTURE").unwrap();
        let config = Configuration::from_json(&json!({"workspaces":{"demo":{"root":"workspace","allowed_paths":["src/main.rs","Cargo.toml","missing.rs","text.bin","link-file","link-dir/file.rs","directory"]}}}).to_string(),temp.path()).unwrap();
        let clock = Arc::new(ManualClock::default());
        let sink = Arc::new(Mutex::new(SinkState::default()));
        let files = Arc::new(SpyFiles::default());
        let broker = Broker::with_dependencies(
            config.clone(),
            ADMIN,
            clock.clone(),
            Box::new(MemorySink(sink.clone())),
            files.clone(),
        )
        .unwrap();
        Self {
            temp,
            router: app(broker),
            config,
            clock,
            sink,
            files,
        }
    }
    fn target(&self) -> std::path::PathBuf {
        self.temp.path().join("workspace/src/main.rs")
    }
    fn unchanged(&self) {
        assert_eq!(fs::read_to_string(self.target()).unwrap(), INITIAL);
    }
    async fn request(
        &self,
        method: &str,
        path: &str,
        token: Option<&str>,
        body: Option<Value>,
    ) -> (u16, Value) {
        send(self.router.clone(), method, path, token, body).await
    }
    async fn create(&self) -> Session {
        let (status, value) = self
            .request(
                "POST",
                "/v1/admin/tasks",
                Some(ADMIN),
                Some(json!({"workspace_id":"demo"})),
            )
            .await;
        assert_eq!(status, 201, "{value}");
        assert_eq!(value["phase"], "inspect");
        Session {
            id: value["task_id"].as_str().unwrap().into(),
            token: value["agent_token"].as_str().unwrap().into(),
        }
    }
    async fn phase(&self, task: &Session, phase: &str) -> (u16, Value) {
        self.request(
            "POST",
            &format!("/v1/admin/tasks/{}/phase", task.id),
            Some(ADMIN),
            Some(json!({"phase":phase})),
        )
        .await
    }
    async fn lease(&self, task: &Session, path: &str, ttl: u16, uses: u8) -> (u16, Value) {
        self.request(
            "POST",
            &format!("/v1/admin/tasks/{}/grants", task.id),
            Some(ADMIN),
            Some(json!({"tool":"write_file","path":path,"ttl_seconds":ttl,"max_uses":uses})),
        )
        .await
    }
    async fn edit(&self, ttl: u16, uses: u8) -> (Session, String) {
        let task = self.create().await;
        assert_eq!(self.phase(&task, "edit").await.0, 200);
        let (status, grant) = self.lease(&task, "src/main.rs", ttl, uses).await;
        assert_eq!(status, 201, "{grant}");
        (task, grant["grant_id"].as_str().unwrap().into())
    }
    async fn read(&self, task: &Session, path: &str) -> (u16, Value) {
        let before = fs::read(self.target()).unwrap();
        let grants = self.detail(task).await["grants"].clone();
        let result = self
            .request(
                "POST",
                "/v1/tool/execute",
                Some(&task.token),
                Some(json!({"task_id":task.id,"tool":"read_file","path":path})),
            )
            .await;
        assert_eq!(fs::read(self.target()).unwrap(), before);
        assert_eq!(self.detail(task).await["grants"], grants);
        if result.1["reason_code"] != "AUDIT_UNAVAILABLE" {
            self.assert_audited(&result.1, result.1["reason_code"].as_str().unwrap());
        }
        result
    }
    async fn write(
        &self,
        task: &Session,
        path: &str,
        gid: Option<&str>,
        content: &str,
    ) -> (u16, Value) {
        let mut body = json!({"task_id":task.id,"tool":"write_file","path":path,"content":content});
        if let Some(gid) = gid {
            body["grant_id"] = json!(gid);
        }
        let before = fs::read(self.target()).unwrap();
        let grants = self.detail(task).await["grants"].clone();
        let result = self
            .request("POST", "/v1/tool/execute", Some(&task.token), Some(body))
            .await;
        if result.1["executed"] == false {
            assert_eq!(fs::read(self.target()).unwrap(), before);
            assert_eq!(self.detail(task).await["grants"], grants);
        }
        if result.1["reason_code"] != "AUDIT_UNAVAILABLE" {
            self.assert_audited(&result.1, result.1["reason_code"].as_str().unwrap());
        }
        result
    }
    async fn detail(&self, task: &Session) -> Value {
        let (status, value) = self
            .request(
                "GET",
                &format!("/v1/admin/tasks/{}", task.id),
                Some(ADMIN),
                None,
            )
            .await;
        assert_eq!(status, 200);
        value
    }
    async fn remaining(&self, task: &Session) -> u64 {
        self.detail(task).await["grants"][0]["remaining_uses"]
            .as_u64()
            .unwrap()
    }
    fn fail_audit_in(&self, distance: usize) {
        let mut sink = self.sink.lock().unwrap();
        sink.fail_at = Some(sink.calls + distance);
    }
    fn logged(&self) -> Vec<Value> {
        self.sink
            .lock()
            .unwrap()
            .lines
            .iter()
            .map(|line| serde_json::from_slice(line).unwrap())
            .collect()
    }
    fn assert_audited(&self, response: &Value, code: &str) {
        let events = self.logged();
        let event = events
            .iter()
            .find(|e| e["request_id"] == response["request_id"] && e["event_type"] == "result")
            .expect("missing result audit");
        assert_eq!(event["reason_code"], code);
        assert_eq!(event["executed"], response["executed"]);
    }
}

async fn send(
    router: Router,
    method: &str,
    path: &str,
    token: Option<&str>,
    body: Option<Value>,
) -> (u16, Value) {
    let bytes = body.map(|v| v.to_string()).unwrap_or_default();
    send_raw(router, method, path, token, bytes).await
}
async fn send_raw(
    router: Router,
    method: &str,
    path: &str,
    token: Option<&str>,
    body: String,
) -> (u16, Value) {
    let mut request = Request::builder()
        .method(method)
        .uri(path)
        .header("content-type", "application/json");
    if let Some(token) = token {
        request = request.header("authorization", format!("Bearer {token}"));
    }
    let response = router
        .oneshot(request.body(Body::from(body)).unwrap())
        .await
        .unwrap();
    let status = response.status().as_u16();
    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    (
        status,
        serde_json::from_slice(&bytes)
            .unwrap_or_else(|e| panic!("non-JSON response ({status}): {e}")),
    )
}
fn rejected(result: (u16, Value), status: u16, code: &str) -> Value {
    assert_eq!(result.0, status, "{}", result.1);
    assert_eq!(result.1["reason_code"], code);
    assert!(!result.1["executed"].as_bool().unwrap());
    result.1
}

#[tokio::test]
async fn a01_registered_task_has_unique_256_bit_token_returned_once() {
    let f = Fixture::new();
    let a = f.create().await;
    let b = f.create().await;
    assert_ne!(a.id, b.id);
    assert_ne!(a.token, b.token);
    assert_eq!(a.token.len(), 64);
    assert!(a.token.bytes().all(|b| b.is_ascii_hexdigit()));
    assert!(f.detail(&a).await.get("agent_token").is_none());
}
#[tokio::test]
async fn a02_inspect_read_returns_real_utf8_content() {
    let f = Fixture::new();
    let task = f.create().await;
    let (s, v) = f.read(&task, "src/main.rs").await;
    assert_eq!(s, 200);
    assert_eq!(v["data"]["content"], INITIAL);
    assert_eq!(v["data"]["bytes_read"], INITIAL.len());
    assert_eq!(v["allowed"], true);
    assert_eq!(v["executed"], true);
    f.assert_audited(&v, "OK");
}
#[tokio::test]
async fn a03_admin_advances_inspect_to_edit() {
    let f = Fixture::new();
    let t = f.create().await;
    assert_eq!(f.phase(&t, "edit").await.0, 200);
    assert_eq!(f.detail(&t).await["phase"], "edit");
}
#[tokio::test]
async fn a04_grant_has_exact_path_bounds_and_snapshot() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    let d = f.detail(&t).await;
    assert_eq!(d["grants"][0]["grant_id"], g);
    assert_eq!(d["grants"][0]["path"], "src/main.rs");
    assert_eq!(d["grants"][0]["status"], "active");
    assert_eq!(d["grants"][0]["remaining_uses"], 1);
    let expiry =
        chrono::DateTime::parse_from_rfc3339(d["grants"][0]["expires_at"].as_str().unwrap())
            .unwrap();
    assert_eq!(expiry.timestamp(), 1_700_000_060);
}
#[tokio::test]
async fn a05_successful_write_changes_only_target_and_consumes_once() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    let (s, v) = f.write(&t, "src/main.rs", Some(&g), "changed").await;
    assert_eq!(s, 200);
    assert_eq!(v["reason_code"], "OK");
    assert_eq!(v["data"]["bytes_written"], 7);
    assert_eq!(f.remaining(&t).await, 0);
    assert_eq!(fs::read_to_string(f.target()).unwrap(), "changed");
    assert_eq!(
        fs::read_to_string(f.temp.path().join("secret.txt")).unwrap(),
        "SIMULATED_PRIVATE_FIXTURE"
    );
    f.assert_audited(&v, "OK");
    let pair: Vec<_> = f
        .logged()
        .into_iter()
        .filter(|e| e["request_id"] == v["request_id"])
        .collect();
    assert_eq!(pair.len(), 2);
    assert_eq!(pair[0]["event_type"], "intent");
    assert_eq!(pair[0]["reason_code"], "PENDING");
}
#[tokio::test]
async fn a06_edit_read_returns_current_content() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    f.write(&t, "src/main.rs", Some(&g), "new").await;
    assert_eq!(f.read(&t, "src/main.rs").await.1["data"]["content"], "new");
}
#[tokio::test]
async fn a07_leaving_edit_immediately_disables_all_leases() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    assert_eq!(f.phase(&t, "verify").await.0, 200);
    assert_eq!(f.detail(&t).await["grants"][0]["status"], "stage_inactive");
    rejected(
        f.write(&t, "src/main.rs", Some(&g), "bad").await,
        403,
        "INVALID_STAGE",
    );
    f.unchanged();
    assert_eq!(f.remaining(&t).await, 1);
}
#[tokio::test]
async fn a08_verify_reads_the_actual_modified_file() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    f.write(&t, "src/main.rs", Some(&g), "verified").await;
    f.phase(&t, "verify").await;
    assert_eq!(
        f.read(&t, "src/main.rs").await.1["data"]["content"],
        "verified"
    );
}
#[tokio::test]
async fn a09_admin_can_complete_the_task() {
    let f = Fixture::new();
    let (t, _) = f.edit(60, 1).await;
    f.phase(&t, "verify").await;
    assert_eq!(f.phase(&t, "done").await.0, 200);
    assert_eq!(f.detail(&t).await["phase"], "done");
}
#[tokio::test]
async fn a10_event_pagination_is_ordered_complete_and_instance_scoped() {
    let f = Fixture::new();
    let (t, _) = f.edit(60, 1).await;
    f.read(&t, "src/main.rs").await;
    let mut after = "0".to_owned();
    let mut ids = Vec::new();
    loop {
        let (s, v) = f
            .request(
                "GET",
                &format!("/v1/admin/events?task_id={}&limit=2&after={after}", t.id),
                Some(ADMIN),
                None,
            )
            .await;
        assert_eq!(s, 200, "{v}");
        for e in v["events"].as_array().unwrap() {
            ids.push(e["event_id"].as_u64().unwrap());
        }
        after = v["next_cursor"].as_str().unwrap().into();
        if v["has_more"] == false {
            break;
        }
    }
    assert_eq!(
        ids,
        f.logged()
            .iter()
            .map(|e| e["event_id"].as_u64().unwrap())
            .collect::<Vec<_>>()
    );
    assert!(ids.windows(2).all(|pair| pair[0] < pair[1]));
    rejected(
        f.request("GET", "/v1/admin/events?after=other:3", Some(ADMIN), None)
            .await,
        400,
        "INVALID_REQUEST",
    );
}

#[tokio::test]
async fn d01_invalid_token_precedes_json_and_logs_no_untrusted_resources() {
    let f = Fixture::new();
    for token in [None, Some("unknown-token")] {
        let v = rejected(
            send_raw(
                f.router.clone(),
                "POST",
                "/v1/tool/execute",
                token,
                "not JSON".into(),
            )
            .await,
            401,
            "INVALID_TOKEN",
        );
        f.assert_audited(&v, "INVALID_TOKEN");
    }
    for e in f.logged() {
        assert!(e["task_id"].is_null());
        assert!(e["path"].is_null());
        assert!(e["phase"].is_null());
    }
    assert_eq!(f.files.reads.load(Ordering::SeqCst), 0);
    assert_eq!(f.files.writes.load(Ordering::SeqCst), 0);
}
#[tokio::test]
async fn d02_agent_cannot_access_any_admin_route_and_admin_cannot_execute_tools() {
    let f = Fixture::new();
    let t = f.create().await;
    for path in [
        "/v1/admin/tasks",
        "/v1/admin/events",
        &format!("/v1/admin/tasks/{}", t.id),
    ] {
        rejected(
            f.request("GET", path, Some(&t.token), None).await,
            403,
            "ROLE_DENIED",
        );
    }
    rejected(
        f.request("POST", "/v1/tool/execute", Some(ADMIN), Some(json!({})))
            .await,
        403,
        "ROLE_DENIED",
    );
    f.unchanged();
}
#[tokio::test]
async fn d03_valid_agent_cannot_probe_another_or_nonexistent_task() {
    let f = Fixture::new();
    let a = f.create().await;
    let b = f.create().await;
    for id in [&b.id, "task_nonexistent"] {
        rejected(
            f.request(
                "POST",
                "/v1/tool/execute",
                Some(&a.token),
                Some(json!({"task_id":id,"tool":"read_file","path":"src/main.rs"})),
            )
            .await,
            403,
            "TASK_TOKEN_MISMATCH",
        );
    }
    assert_eq!(f.files.reads.load(Ordering::SeqCst), 0);
}
#[tokio::test]
async fn d04_unknown_tool_is_400_before_task_binding() {
    let f = Fixture::new();
    let t = f.create().await;
    rejected(
        f.request(
            "POST",
            "/v1/tool/execute",
            Some(&t.token),
            Some(json!({"task_id":"other","tool":"shell","path":"src/main.rs"})),
        )
        .await,
        400,
        "UNKNOWN_TOOL",
    );
}
#[tokio::test]
async fn d05_inspect_write_is_rejected_without_side_effects() {
    let f = Fixture::new();
    let t = f.create().await;
    let v = rejected(
        f.write(&t, "src/main.rs", None, "bad").await,
        403,
        "INVALID_STAGE",
    );
    f.assert_audited(&v, "INVALID_STAGE");
    f.unchanged();
}
#[tokio::test]
async fn d06_verify_write_denial_precedes_expired_lease() {
    let f = Fixture::new();
    let (t, g) = f.edit(1, 1).await;
    f.phase(&t, "verify").await;
    f.clock.nanos.store(2_000_000_000, Ordering::SeqCst);
    rejected(
        f.write(&t, "src/main.rs", Some(&g), "bad").await,
        403,
        "INVALID_STAGE",
    );
    f.unchanged();
    assert_eq!(f.remaining(&t).await, 1);
}
#[tokio::test]
async fn d07_done_read_is_denied() {
    let f = Fixture::new();
    let (t, _) = f.edit(60, 1).await;
    f.phase(&t, "verify").await;
    f.phase(&t, "done").await;
    rejected(f.read(&t, "src/main.rs").await, 403, "INVALID_STAGE");
    assert_eq!(f.files.reads.load(Ordering::SeqCst), 0);
}
#[tokio::test]
async fn d08_repeated_backward_and_skipped_phase_changes_preserve_state() {
    let f = Fixture::new();
    let t = f.create().await;
    for p in ["inspect", "verify", "done"] {
        rejected(f.phase(&t, p).await, 409, "INVALID_TRANSITION");
        assert_eq!(f.detail(&t).await["phase"], "inspect");
    }
    f.phase(&t, "edit").await;
    f.phase(&t, "verify").await;
    rejected(f.phase(&t, "edit").await, 409, "INVALID_TRANSITION");
    f.phase(&t, "done").await;
    rejected(f.phase(&t, "inspect").await, 409, "INVALID_TRANSITION");
}
#[tokio::test]
async fn d09_edit_write_requires_explicit_grant_id() {
    let f = Fixture::new();
    let (t, _) = f.edit(60, 1).await;
    rejected(
        f.write(&t, "src/main.rs", None, "bad").await,
        403,
        "LEASE_REQUIRED",
    );
    f.unchanged();
    assert_eq!(f.remaining(&t).await, 1);
}
#[tokio::test]
async fn d10_exact_expiry_rejects_without_sleep() {
    let f = Fixture::new();
    let (t, g) = f.edit(1, 1).await;
    f.clock.nanos.store(1_000_000_000, Ordering::SeqCst);
    rejected(
        f.write(&t, "src/main.rs", Some(&g), "bad").await,
        403,
        "LEASE_EXPIRED",
    );
    f.unchanged();
    assert_eq!(f.remaining(&t).await, 1);
}
#[tokio::test]
async fn d11_revoked_lease_is_denied_and_revoke_is_idempotent() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    for _ in 0..2 {
        assert_eq!(
            f.request(
                "DELETE",
                &format!("/v1/admin/tasks/{}/grants/{g}", t.id),
                Some(ADMIN),
                None
            )
            .await
            .0,
            200
        );
    }
    rejected(
        f.write(&t, "src/main.rs", Some(&g), "bad").await,
        403,
        "LEASE_REVOKED",
    );
    f.unchanged();
    assert_eq!(f.remaining(&t).await, 1);
}
#[tokio::test]
async fn d12_exhausted_lease_cannot_replace_file_again() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    f.write(&t, "src/main.rs", Some(&g), "first").await;
    rejected(
        f.write(&t, "src/main.rs", Some(&g), "second").await,
        403,
        "LEASE_USED_UP",
    );
    assert_eq!(fs::read_to_string(f.target()).unwrap(), "first");
    assert_eq!(f.remaining(&t).await, 0);
}
#[tokio::test]
async fn d13_lease_for_another_allowed_path_is_denied() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    rejected(
        f.write(&t, "Cargo.toml", Some(&g), "bad").await,
        403,
        "LEASE_MISMATCH",
    );
    assert_eq!(
        fs::read_to_string(f.temp.path().join("workspace/Cargo.toml")).unwrap(),
        "fixture"
    );
    assert_eq!(f.remaining(&t).await, 1);
}
#[tokio::test]
async fn d14_unlisted_path_does_not_return_content() {
    let f = Fixture::new();
    let t = f.create().await;
    let v = rejected(f.read(&t, "secret.txt").await, 403, "PATH_DENIED");
    assert!(v["data"].is_null());
    assert_eq!(f.files.reads.load(Ordering::SeqCst), 0);
}
#[tokio::test]
async fn d15_absolute_path_is_denied_on_all_hosts() {
    let f = Fixture::new();
    let t = f.create().await;
    for p in ["/etc/passwd", "C:/Windows/win.ini"] {
        rejected(f.read(&t, p).await, 403, "PATH_DENIED");
    }
    f.unchanged();
}
#[tokio::test]
async fn d16_parent_traversal_cannot_read_private_fixture() {
    let f = Fixture::new();
    let t = f.create().await;
    rejected(f.read(&t, "../secret.txt").await, 403, "PATH_DENIED");
    assert_eq!(f.files.reads.load(Ordering::SeqCst), 0);
}
#[cfg(unix)]
#[tokio::test]
async fn d17_file_symlink_is_denied() {
    let f = Fixture::new();
    std::os::unix::fs::symlink(
        f.temp.path().join("secret.txt"),
        f.temp.path().join("workspace/link-file"),
    )
    .unwrap();
    let t = f.create().await;
    rejected(f.read(&t, "link-file").await, 403, "PATH_DENIED");
    assert_eq!(f.files.reads.load(Ordering::SeqCst), 0);
}
#[cfg(unix)]
#[tokio::test]
async fn d18_parent_symlink_is_denied() {
    let f = Fixture::new();
    std::os::unix::fs::symlink(f.temp.path(), f.temp.path().join("workspace/link-dir")).unwrap();
    let t = f.create().await;
    rejected(f.read(&t, "link-dir/file.rs").await, 403, "PATH_DENIED");
}
#[tokio::test]
async fn d19_string_prefix_cannot_expand_allowlist() {
    let f = Fixture::new();
    let t = f.create().await;
    rejected(f.read(&t, "src/main.rs.bak").await, 403, "PATH_DENIED");
}
#[tokio::test]
async fn d20_backslash_null_and_dot_segments_are_denied() {
    let f = Fixture::new();
    let t = f.create().await;
    for p in [
        "src\\main.rs",
        "src/ma\0in.rs",
        "src//main.rs",
        "src/./main.rs",
    ] {
        rejected(f.read(&t, p).await, 403, "PATH_DENIED");
    }
}
#[tokio::test]
async fn d21_content_limit_counts_utf8_bytes_and_body_limit_precedes_auth() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    rejected(
        f.write(&t, "src/main.rs", Some(&g), &"中".repeat(21_846))
            .await,
        413,
        "PAYLOAD_TOO_LARGE",
    );
    rejected(
        send_raw(
            f.router.clone(),
            "POST",
            "/v1/tool/execute",
            None,
            " ".repeat(512 * 1024 + 1),
        )
        .await,
        413,
        "PAYLOAD_TOO_LARGE",
    );
    f.unchanged();
    assert_eq!(f.remaining(&t).await, 1);
    assert_eq!(f.files.writes.load(Ordering::SeqCst), 0);
}
#[tokio::test]
async fn d22_non_utf8_is_allowed_but_unexecuted() {
    let f = Fixture::new();
    fs::write(f.temp.path().join("workspace/text.bin"), [0xff, 0xfe]).unwrap();
    let t = f.create().await;
    let v = rejected(f.read(&t, "text.bin").await, 422, "NON_UTF8_CONTENT");
    assert_eq!(v["allowed"], true);
    f.assert_audited(&v, "NON_UTF8_CONTENT");
}
#[tokio::test]
async fn d23_restart_invalidates_tokens_and_instance_cursors() {
    let f = Fixture::new();
    let t = f.create().await;
    let restarted = Broker::with_dependencies(
        f.config.clone(),
        ADMIN,
        Arc::new(ManualClock::default()),
        Box::new(MemorySink(Arc::new(Mutex::new(SinkState::default())))),
        Arc::new(LocalFiles),
    )
    .unwrap();
    rejected(
        send(
            app(restarted),
            "POST",
            "/v1/tool/execute",
            Some(&t.token),
            Some(json!({"task_id":t.id,"tool":"read_file","path":"src/main.rs"})),
        )
        .await,
        401,
        "INVALID_TOKEN",
    );
}
#[tokio::test]
async fn d24_active_grant_conflicts_but_exhausted_grant_can_be_replaced() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    rejected(
        f.lease(&t, "src/main.rs", 60, 1).await,
        409,
        "GRANT_CONFLICT",
    );
    f.write(&t, "src/main.rs", Some(&g), "first").await;
    let (s, v) = f.lease(&t, "src/main.rs", 60, 1).await;
    assert_eq!(s, 201);
    assert_ne!(v["grant_id"], g);
}

#[tokio::test]
async fn c01_ten_concurrent_single_use_writes_have_exactly_one_success() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    let mut handles = Vec::new();
    for i in 0..10 {
        let router = f.router.clone();
        let id = t.id.clone();
        let token = t.token.clone();
        let gid = g.clone();
        handles.push(tokio::spawn(async move { let content=format!("writer-{i}"); let response=send(router,"POST","/v1/tool/execute",Some(&token),Some(json!({"task_id":id,"tool":"write_file","path":"src/main.rs","grant_id":gid,"content":content}))).await; (content,response) }));
    }
    let mut successes = Vec::new();
    let mut denied = 0;
    for h in handles {
        let (content, response) = h.await.unwrap();
        if response.0 == 200 {
            successes.push(content);
        } else {
            rejected(response, 403, "LEASE_USED_UP");
            denied += 1;
        }
    }
    assert_eq!(successes.len(), 1);
    assert_eq!(denied, 9);
    assert_eq!(f.remaining(&t).await, 0);
    assert_eq!(fs::read_to_string(f.target()).unwrap(), successes[0]);
}
#[tokio::test]
async fn c02_monotonic_expiry_ignores_wall_clock_jumps() {
    let f = Fixture::new();
    let (t, g) = f.edit(1, 2).await;
    f.clock.nanos.store(999_999_999, Ordering::SeqCst);
    f.clock.utc_shift.store(86_400, Ordering::SeqCst);
    assert_eq!(
        f.write(&t, "src/main.rs", Some(&g), "before boundary")
            .await
            .0,
        200
    );
    f.clock.nanos.store(1_000_000_000, Ordering::SeqCst);
    rejected(
        f.write(&t, "src/main.rs", Some(&g), "at boundary").await,
        403,
        "LEASE_EXPIRED",
    );
    f.clock.nanos.store(1_000_000_001, Ordering::SeqCst);
    rejected(
        f.write(&t, "src/main.rs", Some(&g), "after boundary").await,
        403,
        "LEASE_EXPIRED",
    );
    assert_eq!(f.remaining(&t).await, 1);
}
#[tokio::test]
async fn c03_requests_after_successful_revoke_cannot_write() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 10).await;
    assert_eq!(
        f.write(&t, "src/main.rs", Some(&g), "before revocation")
            .await
            .0,
        200
    );
    assert_eq!(
        f.request(
            "DELETE",
            &format!("/v1/admin/tasks/{}/grants/{g}", t.id),
            Some(ADMIN),
            None
        )
        .await
        .0,
        200
    );
    rejected(
        f.write(&t, "src/main.rs", Some(&g), "after revocation")
            .await,
        403,
        "LEASE_REVOKED",
    );
    assert_eq!(f.remaining(&t).await, 9);
    assert_eq!(fs::read_to_string(f.target()).unwrap(), "before revocation");
}
#[tokio::test]
async fn c04_pre_replacement_io_failure_neither_truncates_nor_consumes() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    f.files.fail_write.store(true, Ordering::SeqCst);
    let v = rejected(
        f.write(&t, "src/main.rs", Some(&g), "failure payload")
            .await,
        500,
        "IO_ERROR",
    );
    assert_eq!(v["allowed"], true);
    f.unchanged();
    assert_eq!(f.remaining(&t).await, 1);
    f.assert_audited(&v, "IO_ERROR");
}
#[tokio::test]
async fn c05a_pre_execution_audit_failure_prevents_all_file_execution() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    f.fail_audit_in(1);
    rejected(
        f.write(&t, "src/main.rs", Some(&g), "bad").await,
        503,
        "AUDIT_UNAVAILABLE",
    );
    f.unchanged();
    assert_eq!(f.remaining(&t).await, 1);
    assert_eq!(f.files.writes.load(Ordering::SeqCst), 0);
    assert_eq!(f.request("GET", "/healthz", None, None).await.0, 503);
    rejected(f.read(&t, "src/main.rs").await, 503, "AUDIT_UNAVAILABLE");
    assert_eq!(f.files.reads.load(Ordering::SeqCst), 0);
}
#[tokio::test]
async fn c05b_post_execution_audit_failure_reports_commit_and_latches_unavailable() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    f.fail_audit_in(2);
    let (s, v) = f
        .write(
            &t,
            "src/main.rs",
            Some(&g),
            "committed but audit result failed",
        )
        .await;
    assert_eq!(s, 503);
    assert_eq!(v["reason_code"], "AUDIT_UNAVAILABLE");
    assert_eq!(v["allowed"], true);
    assert_eq!(v["executed"], true);
    assert_eq!(f.remaining(&t).await, 0);
    assert_eq!(
        fs::read_to_string(f.target()).unwrap(),
        "committed but audit result failed"
    );
    assert_eq!(f.logged().last().unwrap()["event_type"], "intent");
    rejected(
        f.write(&t, "src/main.rs", Some(&g), "retry").await,
        503,
        "AUDIT_UNAVAILABLE",
    );
    assert_eq!(f.files.writes.load(Ordering::SeqCst), 1);
}
#[tokio::test]
async fn c05c_failed_admin_audit_does_not_change_phase_grants_or_tasks() {
    let f = Fixture::new();
    let t = f.create().await;
    f.fail_audit_in(1);
    rejected(f.phase(&t, "edit").await, 503, "AUDIT_UNAVAILABLE");
    assert_eq!(f.detail(&t).await["phase"], "inspect");
    rejected(
        f.request(
            "POST",
            "/v1/admin/tasks",
            Some(ADMIN),
            Some(json!({"workspace_id":"demo"})),
        )
        .await,
        503,
        "AUDIT_UNAVAILABLE",
    );
    assert_eq!(
        f.request("GET", "/v1/admin/tasks", Some(ADMIN), None)
            .await
            .1["total"],
        1
    );
}
#[tokio::test]
async fn c06_invalid_json_unknown_fields_missing_file_and_empty_write() {
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    rejected(
        send_raw(
            f.router.clone(),
            "POST",
            "/v1/tool/execute",
            Some(&t.token),
            "{".into(),
        )
        .await,
        400,
        "INVALID_REQUEST",
    );
    rejected(
        f.request(
            "POST",
            "/v1/tool/execute",
            Some(&t.token),
            Some(json!({"task_id":t.id,"tool":"read_file","path":"src/main.rs","phase":"edit"})),
        )
        .await,
        400,
        "INVALID_REQUEST",
    );
    rejected(
        f.request(
            "POST",
            "/v1/admin/tasks",
            Some(ADMIN),
            Some(json!({"workspace_id":"unregistered"})),
        )
        .await,
        400,
        "INVALID_REQUEST",
    );
    let v = rejected(f.read(&t, "missing.rs").await, 404, "FILE_NOT_FOUND");
    assert_eq!(v["allowed"], true);
    assert_eq!(f.write(&t, "src/main.rs", Some(&g), "").await.0, 200);
    assert!(fs::read(f.target()).unwrap().is_empty());
    assert_eq!(f.remaining(&t).await, 0);
}

#[tokio::test]
async fn queries_redact_credentials_host_paths_and_content_and_stably_sort_tasks() {
    let f = Fixture::new();
    let a = f.create().await;
    let b = f.create().await;
    let (s, list) = f
        .request("GET", "/v1/admin/tasks?limit=1", Some(ADMIN), None)
        .await;
    assert_eq!(s, 200);
    assert_eq!(list["total"], 2);
    assert_eq!(list["truncated"], true);
    assert_eq!(list["items"][0]["task_id"], b.id);
    let detail = f.detail(&a).await;
    assert!(detail["allowed_paths"].is_array());
    assert!(detail["server_time"].is_string());
    f.read(&a, "src/main.rs").await;
    let (s, events) = f
        .request("GET", "/v1/admin/events", Some(ADMIN), None)
        .await;
    assert_eq!(s, 200);
    for value in [list, detail, events] {
        let text = value.to_string();
        for forbidden in [
            &a.token,
            &b.token,
            ADMIN,
            INITIAL,
            f.temp.path().to_str().unwrap(),
        ] {
            assert!(!text.contains(forbidden));
        }
    }
}
#[tokio::test]
async fn bounds_extra_fields_and_method_errors_are_json() {
    let f = Fixture::new();
    let (t, _) = f.edit(60, 1).await;
    for (ttl, uses) in [(0, 1), (301, 1), (1, 0), (1, 11)] {
        rejected(
            f.lease(&t, "Cargo.toml", ttl, uses).await,
            400,
            "INVALID_REQUEST",
        );
    }
    rejected(
        f.request("GET", "/v1/admin/tasks?limit=101", Some(ADMIN), None)
            .await,
        400,
        "INVALID_REQUEST",
    );
    rejected(
        f.request("GET", "/v1/admin/events?limit=501", Some(ADMIN), None)
            .await,
        400,
        "INVALID_REQUEST",
    );
    rejected(
        f.request("GET", "/v1/admin/events?result=failed", Some(ADMIN), None)
            .await,
        400,
        "INVALID_REQUEST",
    );
    rejected(
        f.request("GET", "/v1/admin/tasks/task_absent", Some(ADMIN), None)
            .await,
        404,
        "TASK_NOT_FOUND",
    );
    rejected(
        f.request("GET", "/v1/tool/execute", Some(&t.token), None)
            .await,
        405,
        "METHOD_NOT_ALLOWED",
    );
    rejected(
        f.request("GET", "/absent", None, None).await,
        404,
        "NOT_FOUND",
    );
    assert_eq!(
        f.request("GET", "/healthz", None, None).await.1["status"],
        "ok"
    );
}
#[test]
fn invalid_configuration_and_overlapping_audit_path_fail_startup() {
    let f = Fixture::new();
    assert!(Configuration::from_json("{", f.temp.path()).is_err());
    for value in [
        json!({"workspaces":{}}),
        json!({"workspaces":{"demo":{"root":"workspace","allowed_paths":["../x"]}}}),
        json!({"workspaces":{"demo":{"root":"workspace","allowed_paths":["src/main.rs"],"max_content_bytes":65537}}}),
    ] {
        assert!(Configuration::from_json(&value.to_string(), f.temp.path()).is_err());
    }
    let inside = f.temp.path().join("workspace/nested/audit.jsonl");
    assert!(Broker::open(f.config.clone(), ADMIN, &inside).is_err());
    assert!(!inside.exists());
    assert!(!inside.parent().unwrap().exists());
    let disguised = f.temp.path().join("new/../workspace/audit.jsonl");
    assert!(Broker::open(f.config.clone(), ADMIN, &disguised).is_err());
    assert!(!f.temp.path().join("new").exists());
    assert!(
        Broker::with_dependencies(
            f.config.clone(),
            "short",
            f.clock.clone(),
            Box::new(MemorySink(f.sink.clone())),
            f.files.clone()
        )
        .is_err()
    );
}

#[tokio::test]
async fn failed_grant_and_revocation_audits_do_not_commit_mutations() {
    let f = Fixture::new();
    let t = f.create().await;
    f.phase(&t, "edit").await;
    f.fail_audit_in(1);
    rejected(
        f.lease(&t, "src/main.rs", 60, 1).await,
        503,
        "AUDIT_UNAVAILABLE",
    );
    assert!(f.detail(&t).await["grants"].as_array().unwrap().is_empty());
    let f = Fixture::new();
    let (t, g) = f.edit(60, 1).await;
    f.fail_audit_in(1);
    rejected(
        f.request(
            "DELETE",
            &format!("/v1/admin/tasks/{}/grants/{g}", t.id),
            Some(ADMIN),
            None,
        )
        .await,
        503,
        "AUDIT_UNAVAILABLE",
    );
    assert_eq!(f.detail(&t).await["grants"][0]["revoked"], false);
    assert_eq!(f.remaining(&t).await, 1);
}

#[tokio::test]
async fn denial_audit_failure_latches_health_without_execution() {
    let f = Fixture::new();
    let t = f.create().await;
    f.fail_audit_in(1);
    rejected(
        f.write(&t, "src/main.rs", None, "bad").await,
        503,
        "AUDIT_UNAVAILABLE",
    );
    f.unchanged();
    assert_eq!(f.files.writes.load(Ordering::SeqCst), 0);
    assert_eq!(f.request("GET", "/healthz", None, None).await.0, 503);
}

#[tokio::test]
async fn expired_and_revoked_grants_can_be_reissued_with_new_ids() {
    let f = Fixture::new();
    let (t, g) = f.edit(1, 1).await;
    f.clock.nanos.store(1_000_000_000, Ordering::SeqCst);
    let (s, new) = f.lease(&t, "src/main.rs", 60, 1).await;
    assert_eq!(s, 201);
    assert_ne!(new["grant_id"], g);
    let id = new["grant_id"].as_str().unwrap();
    f.request(
        "DELETE",
        &format!("/v1/admin/tasks/{}/grants/{id}", t.id),
        Some(ADMIN),
        None,
    )
    .await;
    let (s, replacement) = f.lease(&t, "src/main.rs", 60, 1).await;
    assert_eq!(s, 201);
    assert_ne!(replacement["grant_id"], id);
}

#[tokio::test]
async fn real_jsonl_is_appendable_redacted_and_current_instance_only() {
    let f = Fixture::new();
    let path = f.temp.path().join("audit.jsonl");
    let broker = Broker::open(f.config.clone(), ADMIN, &path).unwrap();
    let router = app(broker);
    let (_, task) = send(
        router.clone(),
        "POST",
        "/v1/admin/tasks",
        Some(ADMIN),
        Some(json!({"workspace_id":"demo"})),
    )
    .await;
    let token = task["agent_token"].as_str().unwrap();
    let (s, v) = send(
        router,
        "POST",
        "/v1/tool/execute",
        Some(token),
        Some(json!({"task_id":task["task_id"],"tool":"read_file","path":"src/main.rs"})),
    )
    .await;
    assert_eq!(s, 200);
    let text = fs::read_to_string(&path).unwrap();
    assert!(!text.contains(token));
    assert!(!text.contains(ADMIN));
    assert!(!text.contains(INITIAL));
    let records: Vec<Value> = text
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(records.len(), 3);
    assert_eq!(records[1]["request_id"], v["request_id"]);
    assert_eq!(records[2]["event_type"], "result");
    let restarted = app(Broker::open(f.config.clone(), ADMIN, &path).unwrap());
    let (_, current) = send(restarted, "GET", "/v1/admin/events", Some(ADMIN), None).await;
    assert!(current["events"].as_array().unwrap().is_empty());
    assert_ne!(current["instance_id"], records[0]["instance_id"]);
    fs::write(&path, "{partial").unwrap();
    assert!(Broker::open(f.config.clone(), ADMIN, &path).is_err());
}

#[tokio::test]
async fn missing_or_unknown_lease_and_structural_errors_have_stable_priority() {
    let f = Fixture::new();
    let (t, _) = f.edit(60, 1).await;
    rejected(
        f.write(&t, "src/main.rs", Some("grant_unknown"), "bad")
            .await,
        403,
        "LEASE_NOT_FOUND",
    );
    let other = f.create().await;
    f.phase(&other, "edit").await;
    let (_, grant) = f.lease(&other, "src/main.rs", 60, 1).await;
    rejected(
        f.write(&t, "src/main.rs", grant["grant_id"].as_str(), "bad")
            .await,
        403,
        "LEASE_NOT_FOUND",
    );
    rejected(
        f.request(
            "POST",
            "/v1/tool/execute",
            Some(&t.token),
            Some(json!({"task_id":t.id,"tool":"write_file","path":"src/main.rs"})),
        )
        .await,
        400,
        "INVALID_REQUEST",
    );
    rejected(
        f.request(
            "POST",
            "/v1/tool/execute",
            Some(&t.token),
            Some(json!({"task_id":t.id,"tool":"read_file","path":"src/main.rs","grant_id":null})),
        )
        .await,
        400,
        "INVALID_REQUEST",
    );
    f.unchanged();
    assert_eq!(f.remaining(&t).await, 1);
}

struct PausedWrite {
    entered: Mutex<Option<std::sync::mpsc::Sender<()>>>,
    release: Mutex<std::sync::mpsc::Receiver<()>>,
}
impl FileOperations for PausedWrite {
    fn read(&self, path: &Path, limit: usize) -> Result<String, ReasonCode> {
        LocalFiles.read(path, limit)
    }
    fn write(&self, path: &Path, content: &str) -> Result<usize, ReasonCode> {
        if let Some(sender) = self.entered.lock().unwrap().take() {
            sender.send(()).unwrap();
        }
        self.release
            .lock()
            .unwrap()
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| ReasonCode::IoError)?;
        LocalFiles.write(path, content)
    }
}

fn paused_fixture() -> (
    Fixture,
    std::sync::mpsc::Receiver<()>,
    std::sync::mpsc::Sender<()>,
) {
    let mut f = Fixture::new();
    let (entered_send, entered) = std::sync::mpsc::channel();
    let (release, release_recv) = std::sync::mpsc::channel();
    let files = Arc::new(PausedWrite {
        entered: Mutex::new(Some(entered_send)),
        release: Mutex::new(release_recv),
    });
    f.router = app(Broker::with_dependencies(
        f.config.clone(),
        ADMIN,
        f.clock.clone(),
        Box::new(MemorySink(f.sink.clone())),
        files,
    )
    .unwrap());
    (f, entered, release)
}

#[tokio::test]
async fn in_progress_write_finishes_before_revoke_and_is_not_cancelled_by_expiry() {
    let (f, entered, release) = paused_fixture();
    let (task, gid) = f.edit(1, 10).await;
    let router = f.router.clone();
    let token = task.token.clone();
    let id = task.id.clone();
    let grant = gid.clone();
    let write = tokio::spawn(async move {
        send(router,"POST","/v1/tool/execute",Some(&token),Some(json!({"task_id":id,"tool":"write_file","path":"src/main.rs","grant_id":grant,"content":"in progress"}))).await
    });
    tokio::task::spawn_blocking(move || entered.recv_timeout(Duration::from_secs(5)).unwrap())
        .await
        .unwrap();
    f.clock.nanos.store(2_000_000_000, Ordering::SeqCst);
    let router = f.router.clone();
    let url = format!("/v1/admin/tasks/{}/grants/{gid}", task.id);
    let mut revoke =
        tokio::spawn(async move { send(router, "DELETE", &url, Some(ADMIN), None).await });
    assert!(
        tokio::time::timeout(Duration::from_millis(30), &mut revoke)
            .await
            .is_err()
    );
    release.send(()).unwrap();
    assert_eq!(write.await.unwrap().0, 200);
    assert_eq!(revoke.await.unwrap().0, 200);
    assert_eq!(f.remaining(&task).await, 9);
    assert_eq!(fs::read_to_string(f.target()).unwrap(), "in progress");
    rejected(
        f.write(&task, "src/main.rs", Some(&gid), "after revoke")
            .await,
        403,
        "LEASE_REVOKED",
    );
}

#[tokio::test]
async fn cancelled_http_waiter_does_not_cancel_an_entered_file_replacement() {
    let (f, entered, release) = paused_fixture();
    let (task, gid) = f.edit(60, 1).await;
    let router = f.router.clone();
    let token = task.token.clone();
    let id = task.id.clone();
    let write = tokio::spawn(async move {
        send(router,"POST","/v1/tool/execute",Some(&token),Some(json!({"task_id":id,"tool":"write_file","path":"src/main.rs","grant_id":gid,"content":"completed after disconnect"}))).await
    });
    tokio::task::spawn_blocking(move || entered.recv_timeout(Duration::from_secs(5)).unwrap())
        .await
        .unwrap();
    write.abort();
    assert!(write.await.unwrap_err().is_cancelled());
    release.send(()).unwrap();
    assert_eq!(f.remaining(&task).await, 0);
    assert_eq!(
        fs::read_to_string(f.target()).unwrap(),
        "completed after disconnect"
    );
    assert!(
        f.logged().iter().any(|e| e["event_type"] == "result"
            && e["reason_code"] == "OK"
            && e["executed"] == true)
    );
}
