//! New registry and task-scoped queries; all identity checks stay in Rust.

use super::*;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct PreviewWorkspace {
    source_path: std::path::PathBuf,
}

pub(super) async fn preview_workspace(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    body: Result<Json<PreviewWorkspace>, JsonRejection>,
) -> Response {
    let Ok(Json(body)) = body else {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    };
    let worker = broker.clone();
    let request = ctx.clone();
    run(broker, ctx, StatusCode::CREATED, move || {
        worker.preview_workspace(&request, &body.source_path)
    })
    .await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct RegisterWorkspace {
    preview_id: String,
    display_name: String,
    profile: String,
}

pub(super) async fn register_workspace(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    body: Result<Json<RegisterWorkspace>, JsonRejection>,
) -> Response {
    let Ok(Json(body)) = body else {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    };
    if !valid_id(&body.preview_id)
        || body.profile != "readonly_snapshot"
        || body.display_name.trim().is_empty()
        || body.display_name.len() > 256
        || body.display_name.chars().any(char::is_control)
    {
        return failure(broker, ctx, ReasonCode::InvalidRequest, false).await;
    }
    let worker = broker.clone();
    let request = ctx.clone();
    run(broker, ctx, StatusCode::CREATED, move || {
        worker.register_workspace(&request, &body.preview_id, body.display_name)
    })
    .await
}

pub(super) async fn workspaces(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
) -> Response {
    let worker = broker.clone();
    run(broker, ctx, StatusCode::OK, move || {
        worker.list_workspaces()
    })
    .await
}

pub(super) async fn agent_status(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    path: Result<Path<String>, PathRejection>,
) -> Response {
    let Ok(Path(id)) = path else {
        return failure(broker, ctx, ReasonCode::InvalidRequest, true).await;
    };
    if !valid_id(&id) {
        return failure(broker, ctx, ReasonCode::InvalidRequest, true).await;
    }
    let worker = broker.clone();
    let request = ctx.clone();
    run(broker, ctx, StatusCode::OK, move || {
        worker.agent_status(&request, &id)
    })
    .await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct FileQuery {
    #[serde(default)]
    prefix: String,
    cursor: Option<String>,
    #[serde(default = "default_file_limit")]
    limit: usize,
}
fn default_file_limit() -> usize {
    100
}

pub(super) async fn agent_files(
    State(broker): State<Broker>,
    Extension(ctx): Extension<RequestContext>,
    path: Result<Path<String>, PathRejection>,
    query: Result<Query<FileQuery>, QueryRejection>,
) -> Response {
    let (Ok(Path(id)), Ok(Query(query))) = (path, query) else {
        return failure(broker, ctx, ReasonCode::InvalidRequest, true).await;
    };
    if !valid_id(&id) {
        return failure(broker, ctx, ReasonCode::InvalidRequest, true).await;
    }
    let worker = broker.clone();
    let request = ctx.clone();
    run(broker, ctx, StatusCode::OK, move || {
        worker.agent_files(
            &request,
            &id,
            &query.prefix,
            query.cursor.as_deref(),
            query.limit,
        )
    })
    .await
}
