//! Thin HTTP validation and role separation; blocking work uses Tokio's pool.

use crate::{
    Broker,
    broker::{Identity, RequestContext, ToolRequest, ToolResponse},
    config::valid_id,
};
use agentfence_core::{PermissionProfile, Phase, ReasonCode};
use axum::{
    Json, Router,
    body::{Body, to_bytes},
    extract::{
        Extension, OriginalUri, Path, Query, Request, State,
        rejection::{JsonRejection, PathRejection, QueryRejection},
    },
    http::{HeaderMap, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{delete, get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};

const BODY_LIMIT: usize = 512 * 1024;
mod v2;

/// Build the v2.1 routes with bounded bodies and role authentication.
///
/// Broker construction validates configuration, credentials and audit storage.
/// This router performs no listening; the binary and tests use the same app.
pub fn app(broker: Broker) -> Router {
    Router::new()
        .route("/healthz", get(health))
        .route("/v1/admin/tasks", get(list_tasks).post(create_task))
        .route("/v1/admin/tasks/{id}", get(task_detail))
        .route("/v1/admin/tasks/{id}/phase", post(advance))
        .route("/v1/admin/tasks/{id}/grants", post(issue_grant))
        .route("/v1/admin/tasks/{id}/grants/{gid}", delete(revoke))
        .route("/v1/admin/events", get(events))
        .route("/v1/admin/workspace-previews", post(v2::preview_workspace))
        .route(
            "/v1/admin/workspace-previews/{id}/excluded",
            get(v2::preview_exclusions),
        )
        .route(
            "/v1/admin/workspaces",
            get(v2::workspaces).post(v2::register_workspace),
        )
        .route("/v1/agent/tasks/{id}", get(v2::agent_status))
        .route("/v1/agent/tasks/{id}/files", get(v2::agent_files))
        .route("/v1/tool/execute", post(execute))
        .fallback(not_found)
        .method_not_allowed_fallback(method_not_allowed)
        .with_state(broker.clone())
        .layer(middleware::from_fn_with_state(broker, envelope))
}

fn bearer(headers: &HeaderMap) -> Option<&str> {
    let mut values = headers.get_all("authorization").iter();
    let value = values.next()?;
    if values.next().is_some() {
        return None;
    }
    let text = value.to_str().ok()?;
    let (scheme, token) = text.split_once(' ')?;
    if !scheme.eq_ignore_ascii_case("bearer")
        || token.is_empty()
        || token.bytes().any(|b| b.is_ascii_whitespace())
    {
        return None;
    }
    Some(token)
}

async fn envelope(State(broker): State<Broker>, request: Request, next: Next) -> Response {
    let mut ctx = broker.context();
    let path = request.uri().path().to_owned();
    let is_tool = path == "/v1/tool/execute" || path.starts_with("/v1/agent/");
    let is_admin = path.starts_with("/v1/admin/");
    let (parts, body) = request.into_parts();
    // Enforce the transport byte bound before credentials. Structure validation
    // happens after role checks, so invalid tokens cannot probe task details.
    let bytes = match to_bytes(body, BODY_LIMIT).await {
        Ok(bytes) => bytes,
        Err(_) => return failure(broker, ctx, ReasonCode::PayloadTooLarge, is_tool).await,
    };
    if is_admin || is_tool {
        // Token lookup may wait for a task-creation lock. Keep even read-only
        // lock acquisition off Tokio workers, just like the file/audit paths.
        let token = bearer(&parts.headers).map(str::to_owned);
        let worker = broker.clone();
        let identity = match tokio::task::spawn_blocking(move || {
            worker.authenticate(token.as_deref())
        })
        .await
        {
            Ok(identity) => identity,
            Err(_) => return failure(broker, ctx, ReasonCode::IoError, is_tool).await,
        };
        match (identity, is_admin) {
            (Ok(Identity::Admin), true) => {}
            (Ok(Identity::Agent(task)), false) => ctx.agent_task = Some(task),
            (Ok(Identity::Agent(task)), true) => {
                let worker = broker.clone();
                let task_id = task.clone();
                let audit =
                    match tokio::task::spawn_blocking(move || worker.is_readonly_task(&task_id))
                        .await
                    {
                        Ok(Ok(readonly)) => readonly,
                        Ok(Err(code)) => return failure(broker, ctx, code, true).await,
                        Err(_) => return failure(broker, ctx, ReasonCode::IoError, true).await,
                    };
                if audit {
                    ctx.agent_task = Some(task);
                }
                return failure(broker, ctx, ReasonCode::RoleDenied, audit).await;
            }
            (Ok(_), _) => return failure(broker, ctx, ReasonCode::RoleDenied, is_tool).await,
            (Err(code), _) => return failure(broker, ctx, code, is_tool).await,
        }
    }
    let mut request = Request::from_parts(parts, Body::from(bytes));
    request.extensions_mut().insert(ctx);
    next.run(request).await
}

fn tool_response(response: ToolResponse) -> Response {
    let status = StatusCode::from_u16(response.reason_code.status())
        .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    (status, Json(response)).into_response()
}

async fn failure(broker: Broker, ctx: RequestContext, code: ReasonCode, audit: bool) -> Response {
    let fallback = broker.reject(&ctx, code, false);
    match tokio::task::spawn_blocking(move || broker.reject(&ctx, code, audit)).await {
        Ok(response) => tool_response(response),
        Err(_) => tool_response(fallback),
    }
}

async fn run<F>(broker: Broker, ctx: RequestContext, status: StatusCode, operation: F) -> Response
where
    F: FnOnce() -> Result<Value, ReasonCode> + Send + 'static,
{
    match tokio::task::spawn_blocking(operation).await {
        Ok(Ok(value)) => (status, Json(value)).into_response(),
        Ok(Err(code)) => failure(broker, ctx, code, false).await,
        Err(_) => failure(broker, ctx, ReasonCode::IoError, false).await,
    }
}

async fn health(State(broker): State<Broker>) -> Response {
    // Reading the latched state shares the audit mutex with durable appends.
    let worker = broker.clone();
    match tokio::task::spawn_blocking(move || worker.healthy()).await {
        Ok(true) => Json(json!({"status":"ok","instance_id":broker.instance_id()})).into_response(),
        Ok(false) => {
            failure(
                broker.clone(),
                broker.context(),
                ReasonCode::AuditUnavailable,
                false,
            )
            .await
        }
        Err(_) => failure(broker.clone(), broker.context(), ReasonCode::IoError, false).await,
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CreateTask {
    workspace_id: String,
    #[serde(default)]
    permission_profile: PermissionProfile,
    #[serde(default)]
    goal: String,
}

async fn create_task(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    body: Result<Json<CreateTask>, JsonRejection>,
) -> Response {
    let Ok(Json(body)) = body else {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    };
    if body.goal.len() > 4000 || body.goal.chars().count() > 1000 {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    }
    let worker = broker.clone();
    let request = ctx.clone();
    run(broker, ctx, StatusCode::CREATED, move || {
        worker.create_task(
            &request,
            &body.workspace_id,
            body.permission_profile,
            body.goal,
        )
    })
    .await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TaskQuery {
    #[serde(default = "default_task_limit")]
    limit: usize,
}
fn default_task_limit() -> usize {
    50
}

async fn list_tasks(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    query: Result<Query<TaskQuery>, QueryRejection>,
) -> Response {
    let Ok(Query(query)) = query else {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    };
    if !(1..=100).contains(&query.limit) {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    }
    let worker = broker.clone();
    run(broker, ctx, StatusCode::OK, move || {
        worker.list_tasks(query.limit)
    })
    .await
}

async fn task_detail(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    path: Result<Path<String>, PathRejection>,
) -> Response {
    let Ok(Path(id)) = path else {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    };
    if !valid_id(&id) {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    }
    let worker = broker.clone();
    run(broker, ctx, StatusCode::OK, move || worker.detail(&id)).await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Advance {
    phase: Phase,
}

async fn advance(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<Advance>, JsonRejection>,
) -> Response {
    let (Ok(Path(id)), Ok(Json(body))) = (path, body) else {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    };
    if !valid_id(&id) {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    }
    let worker = broker.clone();
    let request = ctx.clone();
    run(broker, ctx, StatusCode::OK, move || {
        worker.advance(&request, &id, body.phase)
    })
    .await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IssueGrant {
    tool: String,
    path: String,
    #[serde(default = "default_ttl")]
    ttl_seconds: u16,
    #[serde(default = "default_uses")]
    max_uses: u8,
}
fn default_ttl() -> u16 {
    60
}
fn default_uses() -> u8 {
    1
}

async fn issue_grant(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    path: Result<Path<String>, PathRejection>,
    body: Result<Json<IssueGrant>, JsonRejection>,
) -> Response {
    let (Ok(Path(id)), Ok(Json(body))) = (path, body) else {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    };
    if !valid_id(&id)
        || body.tool != "write_file"
        || !(1..=300).contains(&body.ttl_seconds)
        || !(1..=10).contains(&body.max_uses)
    {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    }
    let worker = broker.clone();
    let request = ctx.clone();
    run(broker, ctx, StatusCode::CREATED, move || {
        worker.issue_grant(&request, &id, &body.path, body.ttl_seconds, body.max_uses)
    })
    .await
}

async fn revoke(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    path: Result<Path<(String, String)>, PathRejection>,
    body: Body,
) -> Response {
    let Ok(Path((id, gid))) = path else {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    };
    let bytes = to_bytes(body, BODY_LIMIT).await;
    if !valid_id(&id) || !valid_id(&gid) || !bytes.is_ok_and(|b| b.is_empty()) {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    }
    let worker = broker.clone();
    let request = ctx.clone();
    run(broker, ctx, StatusCode::OK, move || {
        worker.revoke(&request, &id, &gid)
    })
    .await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EventQuery {
    task_id: Option<String>,
    after: Option<String>,
    #[serde(default = "default_event_limit")]
    limit: usize,
}
fn default_event_limit() -> usize {
    100
}

async fn events(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    query: Result<Query<EventQuery>, QueryRejection>,
) -> Response {
    let Ok(Query(query)) = query else {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    };
    if !(1..=500).contains(&query.limit) || query.task_id.as_deref().is_some_and(|id| !valid_id(id))
    {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    }
    let worker = broker.clone();
    run(broker, ctx, StatusCode::OK, move || {
        worker.events(
            query.task_id.as_deref(),
            query.after.as_deref(),
            query.limit,
        )
    })
    .await
}

async fn execute(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    body: Result<Json<Value>, JsonRejection>,
) -> Response {
    let Ok(Json(value)) = body else {
        return failure(broker, ctx, ReasonCode::InvalidRequest, true).await;
    };
    if value.get("tool").and_then(Value::as_str) == Some("read_file")
        && (value.get("content").is_some() || value.get("grant_id").is_some())
    {
        return failure(broker, ctx, ReasonCode::InvalidRequest, true).await;
    }
    let request: ToolRequest = match serde_json::from_value(value) {
        Ok(request) => request,
        Err(_) => return failure(broker, ctx, ReasonCode::InvalidRequest, true).await,
    };
    let worker = broker.clone();
    let request_ctx = ctx.clone();
    match tokio::task::spawn_blocking(move || worker.execute(&request_ctx, request)).await {
        Ok(response) => tool_response(response),
        Err(_) => failure(broker, ctx, ReasonCode::IoError, true).await,
    }
}

async fn not_found(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    OriginalUri(uri): OriginalUri,
) -> Response {
    failure(
        broker,
        ctx,
        ReasonCode::NotFound,
        uri.path() == "/v1/tool/execute",
    )
    .await
}

async fn method_not_allowed(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    OriginalUri(uri): OriginalUri,
) -> Response {
    failure(
        broker,
        ctx,
        ReasonCode::MethodNotAllowed,
        uri.path() == "/v1/tool/execute",
    )
    .await
}
