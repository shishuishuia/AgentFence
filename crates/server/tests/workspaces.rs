//! Imports, snapshot isolation, scoped pagination and hostile HTTP requests.

use agentfence_core::TimePoint;
use agentfence_server::{
    Broker, Configuration, app, audit::FileSink, clock::Clock, files::LocalFiles,
};
use axum::{Router, body::Body, http::Request};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use std::{
    fs,
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::Duration,
};
use tower::ServiceExt;

const ADMIN: &str = "v2-workspace-tests-only-admin-never-use-in-production";
#[derive(Default)]
struct ManualClock(AtomicU64);
impl Clock for ManualClock {
    fn sample(&self) -> TimePoint {
        let seconds = self.0.load(Ordering::SeqCst);
        TimePoint {
            monotonic: Duration::from_secs(seconds),
            utc: chrono::DateTime::from_timestamp(1_700_000_000 + seconds as i64, 0).unwrap(),
        }
    }
}
struct Fixture {
    root: tempfile::TempDir,
    router: Router,
    clock: Arc<ManualClock>,
}
impl Fixture {
    fn new() -> Self {
        let root = tempfile::tempdir().unwrap();
        let clock = Arc::new(ManualClock::default());
        let sink = FileSink::open(&root.path().join("audit.jsonl")).unwrap();
        let broker = Broker::with_dependencies(
            Configuration::empty(),
            ADMIN,
            clock.clone(),
            Box::new(sink),
            Arc::new(LocalFiles),
        )
        .unwrap();
        Self {
            root,
            router: app(broker),
            clock,
        }
    }
    fn project(&self, name: &str, files: &[(&str, &str)]) -> std::path::PathBuf {
        let root = self.root.path().join(name);
        fs::create_dir_all(&root).unwrap();
        for (name, text) in files {
            let path = root.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, text).unwrap();
        }
        root
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
        (
            response.status().as_u16(),
            serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes())
                .unwrap(),
        )
    }
    async fn preview(&self, source: &Path) -> Value {
        let (status, value) = self
            .call(
                "POST",
                "/v1/admin/workspace-previews",
                ADMIN,
                json!({"source_path":source}),
            )
            .await;
        assert_eq!(status, 201, "{value}");
        value
    }
    async fn confirm(&self, preview: &Value) -> (u16, Value) {
        self.call("POST","/v1/admin/workspaces",ADMIN,json!({"preview_id":preview["preview_id"],"display_name":"Review project","profile":"readonly_snapshot"})).await
    }
    async fn import(&self, source: &Path) -> Value {
        let p = self.preview(source).await;
        let (s, v) = self.confirm(&p).await;
        assert_eq!(s, 201, "{v}");
        v
    }
    async fn task(&self, workspace: &Value) -> Value {
        let (s,v)=self.call("POST","/v1/admin/tasks",ADMIN,json!({"workspace_id":workspace["workspace_id"],"permission_profile":"readonly","goal":"Review files"})).await;
        assert_eq!(s, 201, "{v}");
        v
    }
    async fn read(&self, task: &Value, path: &str) -> (u16, Value) {
        self.call(
            "POST",
            "/v1/tool/execute",
            task["agent_token"].as_str().unwrap(),
            json!({"task_id":task["task_id"],"tool":"read_file","path":path}),
        )
        .await
    }
}

#[tokio::test]
async fn two_projects_import_without_source_changes_and_reads_match_approved_hashes() {
    let f = Fixture::new();
    for (name, files) in [
        (
            "rust-project",
            vec![
                ("src/lib.rs", "pub fn answer()->u8{42}\n"),
                ("Cargo.toml", "[package]\nname='fixture'\n"),
                ("README.md", "Example\n"),
            ],
        ),
        (
            "other-project",
            vec![
                ("app/service.rs", "pub struct Service;\n"),
                ("settings/config.yaml", "enabled: true\n"),
                ("notes.txt", "Review me\n"),
            ],
        ),
    ] {
        let source = f.project(name, &files);
        let workspace = f.import(&source).await;
        let task = f.task(&workspace).await;
        assert_eq!(workspace["allowed_file_count"], 3);
        assert_eq!(task["snapshot_id"], workspace["snapshot_id"]);
        for file in workspace["files"].as_array().unwrap() {
            let (s, v) = f.read(&task, file["path"].as_str().unwrap()).await;
            assert_eq!(s, 200, "{v}");
            assert_eq!(v["data"]["sha256"], file["sha256"]);
        }
        fs::write(source.join(files[0].0), "source later changed").unwrap();
        assert_eq!(
            f.read(&task, files[0].0).await.1["data"]["content"],
            files[0].1
        );
        let id = task["task_id"].as_str().unwrap();
        let token = task["agent_token"].as_str().unwrap();
        for path in [
            format!("/v1/agent/tasks/{id}"),
            format!("/v1/agent/tasks/{id}/files"),
        ] {
            let (s, v) = f.call("GET", &path, token, Value::Null).await;
            assert_eq!(s, 200);
            assert!(!v.to_string().contains("source_path"));
            assert!(!v.to_string().contains("agent_token"));
            assert!(!v.to_string().contains(source.to_str().unwrap()));
        }
    }
    let log = fs::read_to_string(f.root.path().join("audit.jsonl")).unwrap();
    assert!(!log.contains("source_path"));
    assert!(!log.contains("pub fn answer"));
}

#[tokio::test]
async fn previews_are_single_use_expire_exactly_and_reject_changed_sources() {
    let f = Fixture::new();
    let source = f.project("project", &[("main.rs", "before")]);
    let p = f.preview(&source).await;
    fs::write(source.join("main.rs"), "after").unwrap();
    assert_eq!(f.confirm(&p).await.1["reason_code"], "SNAPSHOT_CHANGED");
    assert_eq!(f.confirm(&p).await.1["reason_code"], "PREVIEW_EXPIRED");
    let p = f.preview(&source).await;
    f.clock.0.store(300, Ordering::SeqCst);
    assert_eq!(f.confirm(&p).await.1["reason_code"], "PREVIEW_EXPIRED");
    let p = f.preview(&source).await;
    assert_eq!(f.confirm(&p).await.0, 201);
    assert_eq!(f.confirm(&p).await.1["reason_code"], "PREVIEW_EXPIRED");
    assert_eq!(
        f.confirm(&json!({"preview_id":"preview_forged"})).await.1["reason_code"],
        "PREVIEW_EXPIRED"
    );
}

#[tokio::test]
async fn exclusions_are_visible_and_never_readable_by_agents() {
    let f = Fixture::new();
    let source = f.project(
        "project",
        &[
            ("main.rs", "safe"),
            (".env", "SIMULATED_KEY"),
            ("credentials.json", "SIMULATED_CREDENTIAL"),
            ("auth.json", "SIMULATED_AUTH"),
            ("token.json", "SIMULATED_TOKEN"),
            (".git/config", "private"),
            ("target/generated.rs", "generated"),
            ("node_modules/package/index.rs", "dependency"),
            ("image.png", "image"),
        ],
    );
    fs::write(source.join("broken.txt"), [0xff, 0xfe]).unwrap();
    let w = f.import(&source).await;
    assert_eq!(w["allowed_file_count"], 1);
    assert_eq!(w["excluded_entry_count"], 9);
    let task = f.task(&w).await;
    for path in [
        ".env",
        "credentials.json",
        "auth.json",
        "token.json",
        "target/generated.rs",
        "broken.txt",
        "../outside.txt",
        "/etc/passwd",
        "C:\\outside.txt",
        "unlisted.rs",
    ] {
        let (s, v) = f.read(&task, path).await;
        assert_eq!(s, 403, "{v}");
        assert_eq!(v["reason_code"], "PATH_DENIED");
        assert!(v["data"].is_null());
    }
}

#[tokio::test]
async fn snapshots_cannot_be_opened_for_editing_even_by_omitting_profile() {
    let f = Fixture::new();
    let source = f.project("project", &[("main.rs", "safe")]);
    let w = f.import(&source).await;
    for body in [
        json!({"workspace_id":w["workspace_id"]}),
        json!({"workspace_id":w["workspace_id"],"permission_profile":"controlled_edit"}),
    ] {
        assert_eq!(
            f.call("POST", "/v1/admin/tasks", ADMIN, body).await.1["reason_code"],
            "READONLY_TASK"
        );
    }
}

#[tokio::test]
async fn task_metadata_and_cursors_are_bound_to_identity_snapshot_and_filter() {
    let f = Fixture::new();
    let source = f.project(
        "project",
        &[("a.rs", "a"), ("b.rs", "b"), ("src/c.rs", "c")],
    );
    let w = f.import(&source).await;
    let a = f.task(&w).await;
    let b = f.task(&w).await;
    let id = a["task_id"].as_str().unwrap();
    let token = a["agent_token"].as_str().unwrap();
    let route = format!("/v1/agent/tasks/{id}/files");
    let (s, page) = f
        .call("GET", &format!("{route}?limit=1"), token, Value::Null)
        .await;
    assert_eq!(s, 200);
    let cursor = page["next_cursor"].as_str().unwrap();
    let (s, page2) = f
        .call(
            "GET",
            &format!("{route}?cursor={cursor}&limit=1"),
            token,
            Value::Null,
        )
        .await;
    assert_eq!(s, 200);
    assert_ne!(page["files"][0], page2["files"][0]);
    for route in [
        format!("{route}?prefix=src&cursor={cursor}"),
        format!("{route}?cursor={cursor}0"),
        format!(
            "/v1/agent/tasks/{}/files?cursor={cursor}",
            b["task_id"].as_str().unwrap()
        ),
        format!("{route}?limit=0"),
        format!("{route}?limit=201"),
        format!("{route}?prefix=../"),
        format!("{route}?workspace_id=forged"),
    ] {
        assert_ne!(
            f.call("GET", &route, token, Value::Null).await.0,
            200,
            "{route}"
        );
    }
    assert_eq!(
        f.call(
            "GET",
            &format!("/v1/agent/tasks/{}", b["task_id"].as_str().unwrap()),
            token,
            Value::Null
        )
        .await
        .1["reason_code"],
        "TASK_TOKEN_MISMATCH"
    );
    assert_eq!(
        f.call("GET", &route, ADMIN, Value::Null).await.1["reason_code"],
        "ROLE_DENIED"
    );
    assert_eq!(
        f.call("GET", "/v1/admin/workspaces", token, Value::Null)
            .await
            .1["reason_code"],
        "ROLE_DENIED"
    );
    assert_eq!(
        f.call(
            "POST",
            "/v1/admin/workspace-previews",
            token,
            json!({"source_path":source})
        )
        .await
        .1["reason_code"],
        "ROLE_DENIED"
    );
}

#[tokio::test]
async fn import_budgets_fail_explicitly_instead_of_silently_truncating() {
    let f = Fixture::new();
    let source = f.project("large-file", &[("main.rs", "safe")]);
    fs::write(source.join("main.rs"), vec![b'a'; 65537]).unwrap();
    assert_eq!(
        f.call(
            "POST",
            "/v1/admin/workspace-previews",
            ADMIN,
            json!({"source_path":source})
        )
        .await
        .1["reason_code"],
        "IMPORT_LIMIT_EXCEEDED"
    );
    let source = f.project("many-files", &[]);
    for i in 0..1001 {
        fs::write(source.join(format!("{i}.rs")), "small").unwrap();
    }
    assert_eq!(
        f.call(
            "POST",
            "/v1/admin/workspace-previews",
            ADMIN,
            json!({"source_path":source})
        )
        .await
        .1["reason_code"],
        "IMPORT_LIMIT_EXCEEDED"
    );
    let source = f.project("total-bytes", &[]);
    for i in 0..321 {
        fs::write(source.join(format!("{i}.txt")), vec![b'a'; 65536]).unwrap();
    }
    assert_eq!(
        f.call(
            "POST",
            "/v1/admin/workspace-previews",
            ADMIN,
            json!({"source_path":source})
        )
        .await
        .1["reason_code"],
        "IMPORT_LIMIT_EXCEEDED"
    );
}

#[tokio::test]
async fn root_and_confirmation_payloads_do_not_accept_untrusted_shortcuts() {
    let f = Fixture::new();
    assert_eq!(
        f.call(
            "POST",
            "/v1/admin/workspace-previews",
            ADMIN,
            json!({"source_path":"relative/project"})
        )
        .await
        .1["reason_code"],
        "PATH_DENIED"
    );
    assert_eq!(
        f.call(
            "POST",
            "/v1/admin/workspaces",
            ADMIN,
            json!({"source_path":"C:/fake","profile":"readonly_snapshot","display_name":"forged"})
        )
        .await
        .0,
        400
    );
    let source = f.project("project", &[("main.rs", "safe")]);
    let p = f.preview(&source).await;
    assert_eq!(f.call("POST","/v1/admin/workspaces",ADMIN,json!({"preview_id":p["preview_id"],"display_name":"forged","profile":"controlled_edit"})).await.0,400);
    for i in 1..16 {
        let _ = i;
        f.preview(&source).await;
    }
    assert_eq!(
        f.call(
            "POST",
            "/v1/admin/workspace-previews",
            ADMIN,
            json!({"source_path":source})
        )
        .await
        .1["reason_code"],
        "IMPORT_LIMIT_EXCEEDED"
    );
}

#[cfg(any(unix, windows))]
#[tokio::test]
async fn directory_links_are_excluded_and_cannot_be_selected_as_roots() {
    let f = Fixture::new();
    let source = f.project("project", &[("main.rs", "safe")]);
    let outside = f.project("outside", &[("private.rs", "SIMULATED_PRIVATE")]);
    let link = source.join("linked");
    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside, &link).unwrap();
    #[cfg(windows)]
    {
        let result = std::process::Command::new("cmd")
            .args([
                "/C",
                "mklink",
                "/J",
                link.to_str().unwrap(),
                outside.to_str().unwrap(),
            ])
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "could not prepare directory junction"
        );
    }
    let w = f.import(&source).await;
    assert_eq!(w["allowed_file_count"], 1);
    assert!(
        w["excluded"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["reason"] == "link_or_reparse_point")
    );
    let task = f.task(&w).await;
    assert_eq!(
        f.read(&task, "linked/private.rs").await.1["reason_code"],
        "PATH_DENIED"
    );
    assert_eq!(
        f.call(
            "POST",
            "/v1/admin/workspace-previews",
            ADMIN,
            json!({"source_path":link})
        )
        .await
        .1["reason_code"],
        "PATH_DENIED"
    );
}

#[tokio::test]
async fn one_preview_can_commit_exactly_one_workspace_under_concurrent_confirmation() {
    let f = Fixture::new();
    let source = f.project("project", &[("main.rs", "safe")]);
    let preview = f.preview(&source).await;
    let (a, b) = tokio::join!(f.confirm(&preview), f.confirm(&preview));
    let mut statuses = [a.0, b.0];
    statuses.sort();
    assert_eq!(statuses, [201, 409]);
    let (_, list) = f
        .call("GET", "/v1/admin/workspaces", ADMIN, Value::Null)
        .await;
    assert_eq!(list["items"].as_array().unwrap().len(), 1);
}

struct GateSink(Arc<std::sync::atomic::AtomicBool>);
impl agentfence_server::audit::AuditSink for GateSink {
    fn append(&mut self, _: &[u8]) -> std::io::Result<()> {
        if self.0.load(Ordering::SeqCst) {
            Err(std::io::Error::other("injected audit failure"))
        } else {
            Ok(())
        }
    }
}

#[tokio::test]
async fn failed_registration_audit_does_not_commit_a_workspace_and_closes_imports() {
    let root = tempfile::tempdir().unwrap();
    let clock = Arc::new(ManualClock::default());
    let fail = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let broker = Broker::with_dependencies(
        Configuration::empty(),
        ADMIN,
        clock.clone(),
        Box::new(GateSink(fail.clone())),
        Arc::new(LocalFiles),
    )
    .unwrap();
    let f = Fixture {
        root,
        router: app(broker),
        clock,
    };
    let source = f.project("project", &[("main.rs", "safe")]);
    let preview = f.preview(&source).await;
    fail.store(true, Ordering::SeqCst);
    assert_eq!(
        f.confirm(&preview).await.1["reason_code"],
        "AUDIT_UNAVAILABLE"
    );
    let (_, list) = f
        .call("GET", "/v1/admin/workspaces", ADMIN, Value::Null)
        .await;
    assert!(list["items"].as_array().unwrap().is_empty());
    assert_eq!(
        f.call(
            "POST",
            "/v1/admin/workspace-previews",
            ADMIN,
            json!({"source_path":f.root.path().join("missing")})
        )
        .await
        .1["reason_code"],
        "AUDIT_UNAVAILABLE"
    );
}
