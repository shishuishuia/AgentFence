//! Backend-only minimal case against a real child process and TCP socket.

use serde_json::{Value, json};
use std::{
    fs,
    io::{BufRead, BufReader, Read, Write},
    net::{SocketAddr, TcpStream},
    process::{Child, Command, Stdio},
    sync::mpsc,
    time::Duration,
};

const ADMIN: &str = "test-only-tcp-admin-do-not-use-in-production-00000000";

struct Server(Child);
impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn request(
    address: SocketAddr,
    method: &str,
    path: &str,
    token: Option<&str>,
    body: Option<Value>,
) -> (u16, Value) {
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(3)).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let body = body.map(|v| v.to_string()).unwrap_or_default();
    let auth = token
        .map(|t| format!("Authorization: Bearer {t}\r\n"))
        .unwrap_or_default();
    let text = format!(
        "{method} {path} HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\nContent-Type: application/json\r\n{auth}Content-Length: {}\r\n\r\n{body}",
        body.len()
    );
    stream.write_all(text.as_bytes()).unwrap();
    let mut raw = String::new();
    stream.read_to_string(&mut raw).unwrap();
    let (headers, body) = raw.split_once("\r\n\r\n").unwrap();
    let status = headers
        .lines()
        .next()
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap()
        .parse()
        .unwrap();
    (status, serde_json::from_str(body).unwrap())
}

#[test]
fn minimal_backend_flow_uses_real_http_and_files() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().join("workspace");
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(root.join("src/main.rs"), "before").unwrap();
    fs::write(directory.path().join("secret.txt"), "SIMULATED_PRIVATE").unwrap();
    let policy = directory.path().join("policy.json");
    let audit = directory.path().join("audit.jsonl");
    fs::write(
        &policy,
        json!({"workspaces":{"demo":{"root":"workspace","allowed_paths":["src/main.rs"]}}})
            .to_string(),
    )
    .unwrap();
    let child = Command::new(env!("CARGO_BIN_EXE_agentfence-server"))
        .env("AGENTFENCE_ADMIN_TOKEN", ADMIN)
        .env("AGENTFENCE_BIND", "127.0.0.1:0")
        .env("AGENTFENCE_POLICY", &policy)
        .env("AGENTFENCE_AUDIT", &audit)
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut server = Server(child);
    let stderr = server.0.stderr.take().unwrap();
    let (send, receive) = mpsc::channel();
    let reader = std::thread::spawn(move || {
        let mut line = String::new();
        let _ = BufReader::new(stderr).read_line(&mut line);
        let _ = send.send(line);
    });
    let line = receive
        .recv_timeout(Duration::from_secs(10))
        .expect("server did not become ready");
    reader.join().unwrap();
    let address: SocketAddr = line
        .trim()
        .strip_prefix("AgentFence listening on http://")
        .expect("startup failed")
        .parse()
        .unwrap();
    assert_eq!(request(address, "GET", "/healthz", None, None).0, 200);
    let (status, task) = request(
        address,
        "POST",
        "/v1/admin/tasks",
        Some(ADMIN),
        Some(json!({"workspace_id":"demo"})),
    );
    assert_eq!(status, 201);
    let id = task["task_id"].as_str().unwrap();
    let token = task["agent_token"].as_str().unwrap();
    let read = |path: &str| {
        request(
            address,
            "POST",
            "/v1/tool/execute",
            Some(token),
            Some(json!({"task_id":id,"tool":"read_file","path":path})),
        )
    };
    let write = |grant: Option<&str>, content: &str| {
        let mut body =
            json!({"task_id":id,"tool":"write_file","path":"src/main.rs","content":content});
        if let Some(g) = grant {
            body["grant_id"] = json!(g);
        }
        request(address, "POST", "/v1/tool/execute", Some(token), Some(body))
    };
    assert_eq!(read("src/main.rs").1["data"]["content"], "before");
    let denied = write(None, "must not happen");
    assert_eq!(denied.0, 403);
    assert_eq!(denied.1["reason_code"], "INVALID_STAGE");
    assert_eq!(
        fs::read_to_string(root.join("src/main.rs")).unwrap(),
        "before"
    );
    let phase_url = format!("/v1/admin/tasks/{id}/phase");
    assert_eq!(
        request(
            address,
            "POST",
            &phase_url,
            Some(ADMIN),
            Some(json!({"phase":"edit"}))
        )
        .0,
        200
    );
    let (status, grant) = request(
        address,
        "POST",
        &format!("/v1/admin/tasks/{id}/grants"),
        Some(ADMIN),
        Some(json!({"tool":"write_file","path":"src/main.rs","ttl_seconds":60,"max_uses":1})),
    );
    assert_eq!(status, 201);
    let gid = grant["grant_id"].as_str().unwrap();
    let success = write(Some(gid), "after");
    assert_eq!(success.0, 200);
    assert_eq!(success.1["data"]["remaining_uses"], 0);
    let denied = write(Some(gid), "must not replace");
    assert_eq!(denied.0, 403);
    assert_eq!(denied.1["reason_code"], "LEASE_USED_UP");
    assert_eq!(
        fs::read_to_string(root.join("src/main.rs")).unwrap(),
        "after"
    );
    let denied = read("../secret.txt");
    assert_eq!(denied.0, 403);
    assert_eq!(denied.1["reason_code"], "PATH_DENIED");
    assert!(denied.1["data"].is_null());
    assert_eq!(
        request(
            address,
            "POST",
            &phase_url,
            Some(ADMIN),
            Some(json!({"phase":"verify"}))
        )
        .0,
        200
    );
    assert_eq!(read("src/main.rs").1["data"]["content"], "after");
    assert_eq!(write(Some(gid), "bad").1["reason_code"], "INVALID_STAGE");
    assert_eq!(
        request(
            address,
            "POST",
            &phase_url,
            Some(ADMIN),
            Some(json!({"phase":"done"}))
        )
        .0,
        200
    );
    assert_eq!(read("src/main.rs").1["reason_code"], "INVALID_STAGE");
    let (_, events) = request(
        address,
        "GET",
        &format!("/v1/admin/events?task_id={id}"),
        Some(ADMIN),
        None,
    );
    let text = fs::read_to_string(&audit).unwrap();
    assert!(!text.contains(token));
    assert!(!text.contains(ADMIN));
    let records: Vec<Value> = text
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(records, events["events"].as_array().unwrap().clone());
    for event in records {
        assert!(event.get("content").is_none());
    }
    println!("BACKEND_TCP_MINIMAL_PASS");
}
