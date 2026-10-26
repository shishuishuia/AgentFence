//! Synchronous token/audit locks must not stall the asynchronous HTTP executor.

use agentfence_server::{
    Broker, Configuration, app, audit::AuditSink, clock::SystemClock, files::LocalFiles,
};
use axum::{
    Router,
    body::Body,
    http::{Request, StatusCode},
};
use std::{
    future::{Future, poll_fn},
    io,
    pin::pin,
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    task::Poll,
    time::Duration,
};
use tower::ServiceExt;

const ADMIN: &str = "test-only-runtime-liveness-admin-credential";

#[derive(Default)]
struct Gate {
    released: Mutex<bool>,
    changed: Condvar,
    watchdog_fired: AtomicBool,
}

impl Gate {
    fn release(&self) {
        *self.released.lock().unwrap() = true;
        self.changed.notify_all();
    }
}

struct PausedAudit {
    gate: Arc<Gate>,
    entered: Option<mpsc::SyncSender<()>>,
}

impl AuditSink for PausedAudit {
    fn append(&mut self, _: &[u8]) -> io::Result<()> {
        if let Some(entered) = self.entered.take() {
            entered.send(()).map_err(io::Error::other)?;
            let (released, _) = self
                .gate
                .changed
                .wait_timeout_while(
                    self.gate.released.lock().unwrap(),
                    Duration::from_secs(5),
                    |released| !*released,
                )
                .unwrap();
            if !*released {
                // Bound failures without relying on a small wall-clock threshold.
                // The async test must release this gate after polling a request.
                self.gate.watchdog_fired.store(true, Ordering::SeqCst);
            }
        }
        Ok(())
    }
}

struct Fixture {
    _temp: tempfile::TempDir,
    router: Router,
    gate: Arc<Gate>,
    entered: Option<mpsc::Receiver<()>>,
}

impl Fixture {
    fn new() -> Self {
        let temp = tempfile::tempdir().unwrap();
        let config = Configuration::from_json(
            r#"{"workspaces":{"demo":{"root":".","allowed_paths":["file.rs"]}}}"#,
            temp.path(),
        )
        .unwrap();
        let gate = Arc::new(Gate::default());
        let (entered_tx, entered) = mpsc::sync_channel(1);
        let broker = Broker::with_dependencies(
            config,
            ADMIN,
            Arc::new(SystemClock::default()),
            Box::new(PausedAudit {
                gate: gate.clone(),
                entered: Some(entered_tx),
            }),
            Arc::new(LocalFiles),
        )
        .unwrap();
        Self {
            _temp: temp,
            router: app(broker),
            gate,
            entered: Some(entered),
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        self.gate.release();
    }
}

async fn assert_request_yields_during_audit(request: Request<Body>, status: StatusCode) {
    let mut fixture = Fixture::new();
    let create = fixture.router.clone().oneshot(
        Request::post("/v1/admin/tasks")
            .header("authorization", format!("Bearer {ADMIN}"))
            .header("content-type", "application/json")
            .body(Body::from(r#"{"workspace_id":"demo"}"#))
            .unwrap(),
    );
    let creating = tokio::spawn(create);
    // The channel receive runs outside this single-thread async executor.
    // It confirms the audit mutex is held before the request is polled.
    let entered = fixture.entered.take().unwrap();
    tokio::task::spawn_blocking(move || entered.recv_timeout(Duration::from_secs(5)))
        .await
        .unwrap()
        .unwrap();
    let mut pending = pin!(fixture.router.clone().oneshot(request));
    let ready = poll_fn(|cx| {
        Poll::Ready(match pending.as_mut().poll(cx) {
            Poll::Ready(response) => Some(response),
            Poll::Pending => None,
        })
    })
    .await;
    // Polling authentication or /healthz must yield to this continuation while
    // the audit gate is closed. The old synchronous mutex path blocks until
    // the five-second watchdog releases the audit mutex, which makes this fail.
    let blocked_executor = fixture.gate.watchdog_fired.load(Ordering::SeqCst);
    fixture.gate.release();
    assert!(!blocked_executor, "HTTP request blocked the Tokio executor");
    let response = match ready {
        Some(response) => response.unwrap(),
        None => pending.await.unwrap(),
    };
    assert_eq!(response.status(), status);
    assert_eq!(
        creating.await.unwrap().unwrap().status(),
        StatusCode::CREATED
    );
}

#[tokio::test(flavor = "current_thread")]
async fn token_lookup_yields_while_task_creation_audit_is_pending() {
    assert_request_yields_during_audit(
        Request::get("/v1/admin/tasks")
            .header("authorization", "Bearer unknown-agent-token")
            .body(Body::empty())
            .unwrap(),
        StatusCode::UNAUTHORIZED,
    )
    .await;
}

#[tokio::test(flavor = "current_thread")]
async fn health_lookup_yields_while_audit_append_is_pending() {
    assert_request_yields_during_audit(
        Request::get("/healthz").body(Body::empty()).unwrap(),
        StatusCode::OK,
    )
    .await;
}
