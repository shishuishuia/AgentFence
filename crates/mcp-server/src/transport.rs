//! Bounded loopback HTTP transport; no proxy, redirect or raw error forwarding.

use crate::AdapterError;
use reqwest::{Client, RequestBuilder, Url};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::time::Duration;

pub(super) struct BrokerClient {
    http: Client,
    base: String,
    task: String,
    token: String,
    instance: String,
    snapshot: String,
}

fn error(code: &str) -> Value {
    json!({"reason_code":code,"message":"AgentFence operation failed; inspect the task and audit evidence","request_id":null})
}

impl BrokerClient {
    pub async fn connect(base: &str, task: &str, token: String) -> Result<Self, AdapterError> {
        let url = Url::parse(base).map_err(|_| AdapterError("INVALID_BROKER_URL"))?;
        if url.scheme() != "http"
            || url.host_str() != Some("127.0.0.1")
            || url.port().is_none()
            || url.path() != "/"
            || url.query().is_some()
            || url.fragment().is_some()
            || !url.username().is_empty()
            || url.password().is_some()
        {
            return Err(AdapterError("INVALID_BROKER_URL"));
        }
        if task.is_empty()
            || task.len() > 128
            || !task
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
            || !(32..=256).contains(&token.len())
            || !token.bytes().all(|b| b.is_ascii_graphic())
        {
            return Err(AdapterError("INVALID_TASK_CONFIG"));
        }
        let http = Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(2))
            .timeout(Duration::from_secs(10))
            .build()
            .map_err(|_| AdapterError("HTTP_CLIENT_UNAVAILABLE"))?;
        let mut client = Self {
            http,
            base: base.trim_end_matches('/').into(),
            task: task.into(),
            token,
            instance: String::new(),
            snapshot: String::new(),
        };
        let state = client
            .request(client.http.get(client.task_url()))
            .await
            .map_err(|_| AdapterError("TASK_AUTHENTICATION_FAILED"))?;
        if state["task_id"] != task
            || state["permission_profile"] != "readonly"
            || !state["snapshot_id"]
                .as_str()
                .is_some_and(|s| s.starts_with("snap_"))
        {
            return Err(AdapterError("READONLY_SNAPSHOT_REQUIRED"));
        }
        client.instance = state["instance_id"]
            .as_str()
            .ok_or(AdapterError("INVALID_BROKER_RESPONSE"))?
            .into();
        client.snapshot = state["snapshot_id"]
            .as_str()
            .ok_or(AdapterError("INVALID_BROKER_RESPONSE"))?
            .into();
        Ok(client)
    }

    fn task_url(&self) -> String {
        format!("{}/v1/agent/tasks/{}", self.base, self.task)
    }

    async fn request(&self, request: RequestBuilder) -> Result<Value, Value> {
        let mut response = request
            .bearer_auth(&self.token)
            .send()
            .await
            .map_err(|_| error("BROKER_UNAVAILABLE"))?;
        let success = response.status().is_success();
        if response.content_length().is_some_and(|n| n > 1024 * 1024) {
            return Err(error("BROKER_RESPONSE_TOO_LARGE"));
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| error("BROKER_UNAVAILABLE"))?
        {
            if bytes.len() + chunk.len() > 1024 * 1024 {
                return Err(error("BROKER_RESPONSE_TOO_LARGE"));
            }
            bytes.extend_from_slice(&chunk);
        }
        let body: Value =
            serde_json::from_slice(&bytes).map_err(|_| error("INVALID_BROKER_RESPONSE"))?;
        if !self.instance.is_empty() && body["instance_id"] != self.instance {
            return Err(error("BROKER_INSTANCE_CHANGED"));
        }
        if !self.snapshot.is_empty()
            && body
                .get("snapshot_id")
                .is_some_and(|id| id != &self.snapshot)
        {
            return Err(error("BROKER_SNAPSHOT_CHANGED"));
        }
        if !success {
            let code =
                serde_json::from_value::<agentfence_core::ReasonCode>(body["reason_code"].clone())
                    .ok()
                    .and_then(|code| serde_json::to_value(code).ok());
            let mut result = error(
                code.as_ref()
                    .and_then(Value::as_str)
                    .unwrap_or("BROKER_REJECTED"),
            );
            let expected_prefix = format!("req_{}_", self.instance);
            if let Some(id) = body["request_id"].as_str().filter(|s| {
                s.len() <= 256
                    && s.strip_prefix(&expected_prefix).is_some_and(|number| {
                        !number.is_empty() && number.bytes().all(|b| b.is_ascii_digit())
                    })
            }) {
                result["request_id"] = json!(id);
            }
            return Err(result);
        }
        Ok(body)
    }

    pub async fn status(&self) -> Result<Value, Value> {
        let body = self.request(self.http.get(self.task_url())).await?;
        Ok(
            json!({"task_id":body["task_id"],"workspace_id":body["workspace_id"],"snapshot_id":body["snapshot_id"],"permission_profile":body["permission_profile"],"phase":body["phase"],"goal":body["goal"],"allowed_tools":body["allowed_tools"],"request_id":body["request_id"],"instance_id":body["instance_id"]}),
        )
    }

    pub async fn list(
        &self,
        prefix: Option<&str>,
        cursor: Option<&str>,
        limit: Option<usize>,
    ) -> Result<Value, Value> {
        if prefix.is_some_and(|s| s.len() > 1024) || cursor.is_some_and(|s| s.len() > 128) {
            return Err(error("INVALID_ARGUMENTS"));
        }
        let mut query = vec![("limit", limit.unwrap_or(100).to_string())];
        if let Some(value) = prefix {
            query.push(("prefix", value.into()));
        }
        if let Some(value) = cursor {
            query.push(("cursor", value.into()));
        }
        let body = self
            .request(
                self.http
                    .get(format!("{}/files", self.task_url()))
                    .query(&query),
            )
            .await?;
        Ok(
            json!({"task_id":body["task_id"],"snapshot_id":body["snapshot_id"],"files":body["files"],"total":body["total"],"next_cursor":body["next_cursor"],"request_id":body["request_id"],"instance_id":body["instance_id"]}),
        )
    }

    pub async fn read(&self, path: &str) -> Result<Value, Value> {
        if path.len() > 1024 {
            return Err(error("INVALID_ARGUMENTS"));
        }
        let body = self
            .request(
                self.http
                    .post(format!("{}/v1/tool/execute", self.base))
                    .json(&json!({"task_id":self.task,"tool":"read_file","path":path})),
            )
            .await?;
        let data = &body["data"];
        if body["allowed"] != true
            || body["executed"] != true
            || data["snapshot_id"] != self.snapshot
        {
            return Err(error("INVALID_BROKER_RESPONSE"));
        }
        let content = data["content"]
            .as_str()
            .ok_or_else(|| error("INVALID_BROKER_RESPONSE"))?;
        if content.len() > 65536
            || data["sha256"] != format!("{:x}", Sha256::digest(content.as_bytes()))
        {
            return Err(error("INVALID_BROKER_RESPONSE"));
        }
        let numbered = content
            .lines()
            .enumerate()
            .map(|(n, line)| format!("{}: {line}\n", n + 1))
            .collect::<String>();
        Ok(
            json!({"path":path,"content":content,"numbered_content":numbered,"sha256":data["sha256"],"bytes_read":data["bytes_read"],"snapshot_id":self.snapshot,"request_id":body["request_id"],"instance_id":body["instance_id"]}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{
        Router,
        extract::State,
        http::{StatusCode, header},
        response::{IntoResponse, Response},
        routing::get,
    };
    use std::sync::{
        Arc,
        atomic::{AtomicU8, AtomicUsize, Ordering},
    };
    #[derive(Clone, Default)]
    struct Faults {
        mode: Arc<AtomicU8>,
        redirect_hits: Arc<AtomicUsize>,
    }
    async fn status(State(faults): State<Faults>) -> Response {
        let mut value = json!({"task_id":"task_test","permission_profile":"readonly","snapshot_id":"snap_test","instance_id":"inst_test","request_id":"req_inst_test_7","phase":"inspect"});
        match faults.mode.load(Ordering::SeqCst) {
            1 => {
                value["reason_code"] = json!("ROLE_DENIED");
                value["message"] = json!("SIMULATED_PRIVATE_UPSTREAM_ERROR");
                (StatusCode::FORBIDDEN, axum::Json(value)).into_response()
            }
            2 => {
                value["instance_id"] = json!("inst_restarted");
                axum::Json(value).into_response()
            }
            3 => {
                value["padding"] = json!("x".repeat(1024 * 1024));
                axum::Json(value).into_response()
            }
            4 => (
                StatusCode::TEMPORARY_REDIRECT,
                [(header::LOCATION, "/redirect-target")],
                axum::Json(value),
            )
                .into_response(),
            5 => "SIMULATED_PRIVATE_MALFORMED_BODY".into_response(),
            _ => axum::Json(value).into_response(),
        }
    }
    #[tokio::test]
    async fn transport_bounds_redacts_refuses_redirects_and_detects_instance_changes() {
        let faults = Faults::default();
        let observed = faults.clone();
        let router = Router::new()
            .route("/v1/agent/tasks/task_test", get(status))
            .route(
                "/redirect-target",
                get(move || {
                    let observed = observed.clone();
                    async move {
                        observed.redirect_hits.fetch_add(1, Ordering::SeqCst);
                        "unexpected redirect"
                    }
                }),
            )
            .with_state(faults.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
        let client = BrokerClient::connect(
            &url,
            "task_test",
            "test-only-task-credential-0000000000000000".into(),
        )
        .await
        .unwrap();
        for (mode, expected) in [
            (1, "ROLE_DENIED"),
            (2, "BROKER_INSTANCE_CHANGED"),
            (3, "BROKER_RESPONSE_TOO_LARGE"),
            (4, "BROKER_REJECTED"),
            (5, "INVALID_BROKER_RESPONSE"),
        ] {
            faults.mode.store(mode, Ordering::SeqCst);
            let error = client.status().await.unwrap_err();
            assert_eq!(error["reason_code"], expected);
            assert!(!error.to_string().contains("SIMULATED_PRIVATE"));
            if mode == 1 {
                assert_eq!(error["request_id"], "req_inst_test_7");
            }
        }
        assert_eq!(faults.redirect_hits.load(Ordering::SeqCst), 0);
        server.abort();
    }
}
