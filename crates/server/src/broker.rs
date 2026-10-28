//! Task-locked control plane and execution, with no HTTP-specific policy rules.

use crate::{
    audit::{AuditSink, AuditStore, Event, FileSink},
    authorization::Authorization,
    clock::{Clock, SystemClock},
    config::{Configuration, StartupError, Workspace},
    files::{FileOperations, LocalFiles},
    workspace::{self, PREVIEW_TTL, Preview},
};
use agentfence_core::{
    Grant, GrantSpec, GrantStatus, PermissionProfile, Phase, ReasonCode, TimePoint, Tool, ToolCall,
    resolve_existing,
};
use chrono::{DateTime, Utc};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    path::Path,
    sync::{
        Arc, Mutex, MutexGuard,
        atomic::{AtomicU64, Ordering},
    },
    time::Instant,
};
use subtle::ConstantTimeEq;
mod registry;

/// Per-request context created before body validation and authentication.
#[derive(Clone)]
pub struct RequestContext {
    /// Unique within this randomly identified server instance.
    pub request_id: String,
    /// Start of total server handling time.
    pub started: Instant,
    /// Authenticated task identity, never copied from request JSON.
    pub(crate) agent_task: Option<String>,
}

/// Unified tool outcome, also used for errors on other endpoints.
#[derive(Debug, Serialize)]
pub struct ToolResponse {
    /// Correlates this response with audit records.
    pub request_id: String,
    /// Full authorization result (intents do not imply authorization).
    pub allowed: bool,
    /// Whether the file operation completed, including after audit-result failure.
    pub executed: bool,
    /// Stable machine-readable result.
    pub reason_code: ReasonCode,
    /// Safe human-readable explanation.
    pub message: &'static str,
    /// Total server microseconds including final audit append.
    pub duration_us: u64,
    /// Pure-rule microseconds, excluding locks, metadata, file I/O and audit.
    pub decision_us: u64,
    /// Read content/size or write bytes/lease state; null for ordinary failures.
    pub data: Value,
    /// Allows a viewer to recognize restart even on an error response.
    pub instance_id: String,
}

/// Parsed request shape; content and grant fields are checked per tool.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ToolRequest {
    pub task_id: String,
    pub tool: String,
    pub path: String,
    pub grant_id: Option<String>,
    pub content: Option<String>,
}

struct Task {
    task_id: String,
    workspace_id: String,
    phase: Phase,
    profile: PermissionProfile,
    goal: String,
    snapshot_id: Option<String>,
    grants: Vec<Grant>,
    created_at: DateTime<Utc>,
    updated_at: DateTime<Utc>,
    sequence: u64,
}

pub(crate) enum Identity {
    Admin,
    Agent(String),
}

struct Shared {
    workspaces: Mutex<HashMap<String, Arc<Workspace>>>,
    previews: Mutex<HashMap<String, Preview>>,
    admin_hash: [u8; 32],
    tasks: Mutex<HashMap<String, Arc<Mutex<Task>>>>,
    agent_hashes: Mutex<HashMap<[u8; 32], String>>,
    audit: Mutex<AuditStore>,
    clock: Arc<dyn Clock>,
    files: Arc<dyn FileOperations>,
    authorization: Authorization,
    instance_id: String,
    request_sequence: AtomicU64,
}

/// Cloneable application state. Tokens are hashed and have no Debug output.
#[derive(Clone)]
pub struct Broker {
    shared: Arc<Shared>,
}

pub(crate) fn lock<T>(mutex: &Mutex<T>) -> Result<MutexGuard<'_, T>, ReasonCode> {
    mutex.lock().map_err(|_| ReasonCode::AuditUnavailable)
}

fn hash(token: &str) -> [u8; 32] {
    Sha256::digest(token.as_bytes()).into()
}

fn random_hex<const N: usize>() -> Result<String, StartupError> {
    let mut bytes = [0u8; N];
    getrandom::fill(&mut bytes)
        .map_err(|_| StartupError::Invalid("OS random source unavailable"))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn micros(started: Instant) -> u64 {
    started.elapsed().as_micros().min(u128::from(u64::MAX)) as u64
}

impl Broker {
    /// Create real file/audit executors after validating audit isolation.
    ///
    /// Admin Token must be 32..=256 visible ASCII bytes, supplied externally.
    /// Startup fails on invalid policy, unavailable randomness or audit storage.
    pub fn open(
        config: Configuration,
        admin_token: &str,
        audit_path: &Path,
    ) -> Result<Self, StartupError> {
        Self::open_with_authorization(config, admin_token, audit_path, Authorization::Enforced)
    }

    pub(crate) fn open_with_authorization(
        config: Configuration,
        admin_token: &str,
        audit_path: &Path,
        authorization: Authorization,
    ) -> Result<Self, StartupError> {
        let path = config.audit_path(audit_path)?;
        let sink = FileSink::open(&path)?;
        Self::build(
            config,
            admin_token,
            Arc::new(SystemClock::default()),
            Box::new(sink),
            Arc::new(LocalFiles),
            authorization,
        )
    }

    /// Construct with trusted dependencies for fault and exact-time tests.
    ///
    /// These substitutions are library-only and have no HTTP/config selector.
    /// Production callers should use open, which validates the real audit path.
    pub fn with_dependencies(
        config: Configuration,
        admin_token: &str,
        clock: Arc<dyn Clock>,
        sink: Box<dyn AuditSink>,
        files: Arc<dyn FileOperations>,
    ) -> Result<Self, StartupError> {
        Self::build(
            config,
            admin_token,
            clock,
            sink,
            files,
            Authorization::Enforced,
        )
    }

    fn build(
        config: Configuration,
        admin_token: &str,
        clock: Arc<dyn Clock>,
        sink: Box<dyn AuditSink>,
        files: Arc<dyn FileOperations>,
        authorization: Authorization,
    ) -> Result<Self, StartupError> {
        if !(32..=256).contains(&admin_token.len())
            || !admin_token.bytes().all(|b| b.is_ascii_graphic())
        {
            return Err(StartupError::Invalid(
                "AGENTFENCE_ADMIN_TOKEN must contain 32..256 visible ASCII bytes; generate a fresh random secret",
            ));
        }
        let instance_id = format!("inst_{}", random_hex::<16>()?);
        let workspaces = config
            .workspaces
            .into_iter()
            .map(|(id, workspace)| (id, Arc::new(workspace)))
            .collect();
        Ok(Self {
            shared: Arc::new(Shared {
                workspaces: Mutex::new(workspaces),
                previews: Mutex::new(HashMap::new()),
                admin_hash: hash(admin_token),
                tasks: Mutex::new(HashMap::new()),
                agent_hashes: Mutex::new(HashMap::new()),
                audit: Mutex::new(AuditStore::new(instance_id.clone(), sink)),
                clock,
                files,
                authorization,
                instance_id,
                request_sequence: AtomicU64::new(0),
            }),
        })
    }

    /// Create an identifier and start-time sample for one HTTP request.
    #[must_use]
    pub fn context(&self) -> RequestContext {
        let sequence = self.shared.request_sequence.fetch_add(1, Ordering::Relaxed);
        RequestContext {
            request_id: format!("req_{}_{sequence}", self.shared.instance_id),
            started: Instant::now(),
            agent_task: None,
        }
    }

    pub(crate) fn authenticate(&self, token: Option<&str>) -> Result<Identity, ReasonCode> {
        let token = token
            .filter(|s| !s.is_empty() && s.len() <= 256)
            .ok_or(ReasonCode::InvalidToken)?;
        let digest = hash(token);
        if self.shared.admin_hash.ct_eq(&digest).unwrap_u8() == 1 {
            return Ok(Identity::Admin);
        }
        lock(&self.shared.agent_hashes)?
            .get(&digest)
            .cloned()
            .map(Identity::Agent)
            .ok_or(ReasonCode::InvalidToken)
    }

    pub(crate) fn healthy(&self) -> bool {
        lock(&self.shared.audit).is_ok_and(|audit| audit.available)
    }
    pub(crate) fn instance_id(&self) -> &str {
        &self.shared.instance_id
    }

    fn task(&self, id: &str) -> Result<Arc<Mutex<Task>>, ReasonCode> {
        lock(&self.shared.tasks)?
            .get(id)
            .cloned()
            .ok_or(ReasonCode::TaskNotFound)
    }

    pub(crate) fn is_readonly_task(&self, id: &str) -> Result<bool, ReasonCode> {
        let task = self.task(id)?;
        let task = lock(&task)?;
        Ok(task.profile == PermissionProfile::Readonly)
    }

    fn append(&self, event: Event) -> Result<(), ReasonCode> {
        lock(&self.shared.audit)?.append(event)
    }

    fn outcome(
        &self,
        ctx: &RequestContext,
        code: ReasonCode,
        allowed: bool,
        executed: bool,
        data: Value,
        decision_us: u64,
    ) -> ToolResponse {
        ToolResponse {
            request_id: ctx.request_id.clone(),
            allowed,
            executed,
            reason_code: code,
            message: code.message(),
            duration_us: micros(ctx.started),
            decision_us,
            data,
            instance_id: self.shared.instance_id.clone(),
        }
    }

    /// Build an error and, for tool requests, durably record its result.
    ///
    /// Call this for rejected JSON/authentication as well as route errors. No
    /// unvalidated task/path is logged. An audit failure overrides the code.
    pub fn reject(&self, ctx: &RequestContext, code: ReasonCode, audit_tool: bool) -> ToolResponse {
        let mut final_code = code;
        if audit_tool {
            let mut event = Event::new("result", &ctx.request_id, self.shared.clock.sample());
            event.task_id = ctx.agent_task.clone();
            event.reason_code = code;
            event.duration_us = micros(ctx.started);
            if self.append(event).is_err() {
                final_code = ReasonCode::AuditUnavailable;
            }
        }
        self.outcome(ctx, final_code, false, false, Value::Null, 0)
    }

    pub(crate) fn create_task(
        &self,
        ctx: &RequestContext,
        workspace_id: &str,
        profile: PermissionProfile,
        goal: String,
    ) -> Result<Value, ReasonCode> {
        let workspace = self.workspace(workspace_id)?;
        if workspace.snapshot.is_some() && profile != PermissionProfile::Readonly {
            return Err(ReasonCode::ReadonlyTask);
        }
        if profile == PermissionProfile::Readonly && workspace.snapshot.is_none() {
            return Err(ReasonCode::InvalidRequest);
        }
        let task_id = format!(
            "task_{}",
            random_hex::<16>().map_err(|_| ReasonCode::IoError)?
        );
        // 32 OS-random bytes = 256 random bits. A UUID v4 alone is insufficient.
        let token = random_hex::<32>().map_err(|_| ReasonCode::IoError)?;
        let now = self.shared.clock.sample();
        let mut tasks = lock(&self.shared.tasks)?;
        let task = Task {
            task_id: task_id.clone(),
            workspace_id: workspace_id.into(),
            phase: Phase::Inspect,
            profile,
            goal,
            snapshot_id: workspace.snapshot.as_ref().map(|s| s.id.clone()),
            grants: Vec::new(),
            created_at: now.utc,
            updated_at: now.utc,
            sequence: tasks.len() as u64,
        };
        let mut event = Event::new("task_created", &ctx.request_id, now);
        event.task_id = Some(task_id.clone());
        event.phase = Some(Phase::Inspect);
        event.workspace_id = Some(workspace_id.into());
        event.snapshot_id = task.snapshot_id.clone();
        event.allowed = true;
        event.executed = true;
        event.reason_code = ReasonCode::Ok;
        event.duration_us = micros(ctx.started);
        self.append(event)?; // Commit memory only after the management event is confirmed.
        // Token lookups must not wait for this management event's disk flush.
        let mut agents = lock(&self.shared.agent_hashes)?;
        let summary = summary(&task);
        agents.insert(hash(&token), task_id.clone());
        tasks.insert(task_id, Arc::new(Mutex::new(task)));
        let mut response = summary;
        response["agent_token"] = json!(token);
        response["request_id"] = json!(ctx.request_id);
        response["instance_id"] = json!(self.instance_id());
        Ok(response)
    }

    pub(crate) fn list_tasks(&self, limit: usize) -> Result<Value, ReasonCode> {
        let tasks: Vec<_> = lock(&self.shared.tasks)?.values().cloned().collect();
        let total = tasks.len();
        let mut snapshots = Vec::new();
        for task in tasks {
            let task = lock(&task)?;
            snapshots.push((task.created_at, task.sequence, summary(&task)));
        }
        snapshots.sort_by_key(|entry| std::cmp::Reverse((entry.0, entry.1)));
        let items: Vec<_> = snapshots
            .into_iter()
            .take(limit)
            .map(|(_, _, value)| value)
            .collect();
        Ok(
            json!({"items":items,"total":total,"truncated":total>limit,"instance_id":self.instance_id()}),
        )
    }

    pub(crate) fn detail(&self, id: &str) -> Result<Value, ReasonCode> {
        let task = self.task(id)?;
        let task = lock(&task)?;
        let now = self.shared.clock.sample();
        let mut value = summary(&task);
        let workspace = self.workspace(&task.workspace_id)?;
        value["allowed_paths"] = json!(workspace.policy.allowed_paths());
        value["grants"] = json!(
            task.grants
                .iter()
                .map(|g| grant_snapshot(g, task.phase, now))
                .collect::<Vec<_>>()
        );
        value["server_time"] = json!(now.utc);
        value["instance_id"] = json!(self.instance_id());
        Ok(value)
    }

    pub(crate) fn advance(
        &self,
        ctx: &RequestContext,
        id: &str,
        phase: Phase,
    ) -> Result<Value, ReasonCode> {
        let task = self.task(id)?;
        let mut task = lock(&task)?;
        if task.profile == PermissionProfile::Readonly && phase == Phase::Edit {
            self.management_denied(ctx, &task, "advance_phase", ReasonCode::ReadonlyTask)?;
            return Err(ReasonCode::ReadonlyTask);
        }
        if task.phase.next_for(task.profile) != Some(phase) {
            return Err(ReasonCode::InvalidTransition);
        }
        let now = self.shared.clock.sample();
        let mut event = Event::new("phase_changed", &ctx.request_id, now);
        event.task_id = Some(id.into());
        event.phase = Some(phase);
        event.allowed = true;
        event.executed = true;
        event.reason_code = ReasonCode::Ok;
        event.duration_us = micros(ctx.started);
        self.append(event)?;
        task.phase = phase;
        task.updated_at = now.utc;
        Ok(
            json!({"request_id":ctx.request_id,"task_id":id,"phase":phase,"updated_at":task.updated_at,"instance_id":self.instance_id()}),
        )
    }

    pub(crate) fn issue_grant(
        &self,
        ctx: &RequestContext,
        id: &str,
        path: &str,
        ttl: u16,
        uses: u8,
    ) -> Result<Value, ReasonCode> {
        let task = self.task(id)?;
        let mut task = lock(&task)?;
        if task.profile == PermissionProfile::Readonly {
            self.management_denied(ctx, &task, "issue_grant", ReasonCode::ReadonlyTask)?;
            return Err(ReasonCode::ReadonlyTask);
        }
        if task.phase != Phase::Edit {
            return Err(ReasonCode::InvalidStage);
        }
        let workspace = self.workspace(&task.workspace_id)?;
        workspace.policy.check_path(path)?;
        let now = self.shared.clock.sample();
        if task
            .grants
            .iter()
            .any(|g| g.path == path && g.status(task.phase, now.monotonic) == GrantStatus::Active)
        {
            return Err(ReasonCode::GrantConflict);
        }
        let grant = Grant::issue(
            GrantSpec {
                grant_id: format!(
                    "grant_{}",
                    random_hex::<16>().map_err(|_| ReasonCode::IoError)?
                ),
                task_id: id.into(),
                path: path.into(),
                ttl_seconds: ttl,
                max_uses: uses,
            },
            now,
        )?;
        let mut event = Event::new("grant_issued", &ctx.request_id, now);
        event.task_id = Some(id.into());
        event.phase = Some(task.phase);
        event.path = Some(path.into());
        event.tool = Some("write_file".into());
        apply_grant(&mut event, &grant);
        event.allowed = true;
        event.executed = true;
        event.reason_code = ReasonCode::Ok;
        event.duration_us = micros(ctx.started);
        self.append(event)?;
        let mut response = grant_snapshot(&grant, task.phase, now);
        response["request_id"] = json!(ctx.request_id);
        task.grants.push(grant);
        task.updated_at = now.utc;
        Ok(response)
    }

    pub(crate) fn revoke(
        &self,
        ctx: &RequestContext,
        id: &str,
        grant_id: &str,
    ) -> Result<Value, ReasonCode> {
        let task = self.task(id)?;
        let mut task = lock(&task)?;
        let index = task
            .grants
            .iter()
            .position(|g| g.grant_id == grant_id)
            .ok_or(ReasonCode::LeaseNotFound)?;
        let mut next = task.grants[index].clone();
        next.revoked = true;
        let now = self.shared.clock.sample();
        let mut event = Event::new("grant_revoked", &ctx.request_id, now);
        event.task_id = Some(id.into());
        event.phase = Some(task.phase);
        event.tool = Some("write_file".into());
        event.path = Some(next.path.clone());
        apply_grant(&mut event, &next);
        event.allowed = true;
        event.executed = true;
        event.reason_code = ReasonCode::Ok;
        event.duration_us = micros(ctx.started);
        self.append(event)?;
        task.grants[index] = next;
        task.updated_at = now.utc;
        let mut value = grant_snapshot(&task.grants[index], task.phase, now);
        value["request_id"] = json!(ctx.request_id);
        Ok(value)
    }

    pub(crate) fn events(
        &self,
        task_id: Option<&str>,
        after: Option<&str>,
        limit: usize,
    ) -> Result<Value, ReasonCode> {
        if let Some(id) = task_id {
            self.task(id)?;
        }
        let position = match after {
            None | Some("") | Some("0") => 0,
            Some(cursor) => {
                let (instance, number) =
                    cursor.rsplit_once(':').ok_or(ReasonCode::InvalidRequest)?;
                if instance != self.instance_id() {
                    return Err(ReasonCode::InvalidRequest);
                }
                number
                    .parse::<u64>()
                    .map_err(|_| ReasonCode::InvalidRequest)?
            }
        };
        let audit = lock(&self.shared.audit)?;
        let end = audit.events.last().map_or(0, |e| e.event_id);
        if position > end {
            return Err(ReasonCode::InvalidRequest);
        }
        let mut matches = audit.events.iter().filter(|e| {
            e.event_id > position && task_id.is_none_or(|id| e.task_id.as_deref() == Some(id))
        });
        let events: Vec<_> = matches.by_ref().take(limit).cloned().collect();
        let has_more = matches.next().is_some();
        let next = if has_more {
            events.last().map_or(position, |e| e.event_id)
        } else {
            end
        };
        Ok(
            json!({"events":events,"next_cursor":format!("{}:{next}",self.instance_id()),"has_more":has_more,"instance_id":self.instance_id()}),
        )
    }

    pub(crate) fn execute(&self, ctx: &RequestContext, request: ToolRequest) -> ToolResponse {
        let tool = match Tool::parse(&request.tool) {
            Ok(tool) => tool,
            Err(code) => return self.reject(ctx, code, true),
        };
        if !crate::config::valid_id(&request.task_id)
            || request
                .grant_id
                .as_deref()
                .is_some_and(|id| !crate::config::valid_id(id))
            || (tool == Tool::ReadFile && (request.content.is_some() || request.grant_id.is_some()))
            || (tool == Tool::WriteFile && request.content.is_none())
        {
            return self.reject(ctx, ReasonCode::InvalidRequest, true);
        }
        let Some(own_task_id) = ctx.agent_task.as_deref() else {
            return self.reject(ctx, ReasonCode::InvalidToken, true);
        };
        let task = match self.task(own_task_id) {
            Ok(task) => task,
            Err(_) => return self.reject(ctx, ReasonCode::InvalidToken, true),
        };
        let mut task = match lock(&task) {
            Ok(task) => task,
            Err(code) => return self.reject(ctx, code, true),
        };
        let Ok(workspace) = self.workspace(&task.workspace_id) else {
            return self.reject(ctx, ReasonCode::InvalidRequest, true);
        };
        if request
            .content
            .as_ref()
            .is_some_and(|c| c.len() > workspace.policy.max_content_bytes())
        {
            return self.reject(ctx, ReasonCode::PayloadTooLarge, true);
        }
        if own_task_id != request.task_id {
            return self.reject(ctx, ReasonCode::TaskTokenMismatch, true);
        }
        let _workspace_guard = match lock(&workspace.gate) {
            Ok(guard) => guard,
            Err(code) => return self.reject(ctx, code, true),
        };
        // TTL is sampled after entering the complete controlled execution area.
        let now = self.shared.clock.sample();
        let mut event = Event::new("result", &ctx.request_id, now);
        event.task_id = Some(task.task_id.clone());
        event.workspace_id = Some(task.workspace_id.clone());
        event.snapshot_id = task.snapshot_id.clone();
        event.phase = Some(task.phase);
        event.tool = Some(request.tool.clone());
        event.path = Some(request.path.chars().take(256).collect());
        let call = ToolCall {
            tool,
            path: &request.path,
            grant_id: request.grant_id.as_deref(),
        };
        let mut data = Value::Null;
        let mut granted_index = None;
        let mut decision_us = 0;
        let result = (|| -> Result<(), ReasonCode> {
            if !self.healthy() {
                return Err(ReasonCode::AuditUnavailable);
            }
            if task.profile == PermissionProfile::Readonly && tool == Tool::WriteFile {
                return Err(ReasonCode::ReadonlyTask);
            }
            if request
                .content
                .as_ref()
                .is_some_and(|c| c.len() > workspace.policy.max_content_bytes())
            {
                return Err(ReasonCode::PayloadTooLarge);
            }
            let start = Instant::now();
            let resource =
                self.shared
                    .authorization
                    .check_resource(&workspace.policy, task.phase, call);
            decision_us += micros(start);
            resource?;
            // Intent is pending, not an authorization success. It precedes even
            // filesystem metadata access. This preserves path-before-lease error
            // priority without accessing files when the pre-execution audit fails.
            let mut intent = event.clone();
            intent.event_type = "intent".into();
            intent.reason_code = ReasonCode::Pending;
            intent.duration_us = micros(ctx.started);
            intent.decision_us = decision_us;
            self.append(intent)?;
            let resolved = resolve_existing(&workspace.root, &request.path);
            if let Err(agentfence_core::PathError::Denied) = resolved {
                return Err(ReasonCode::PathDenied);
            }
            let start = Instant::now();
            let selected = self.shared.authorization.check_lease(
                &task.task_id,
                &task.grants,
                call,
                now.monotonic,
            );
            decision_us += micros(start);
            let permit = selected?;
            granted_index = permit.grant_index();
            event.allowed = true;
            let path = resolved.map_err(|e| e.reason())?;
            match tool {
                Tool::ReadFile => {
                    let content = self
                        .shared
                        .files
                        .read(&path, workspace.policy.max_content_bytes())?;
                    event.executed = true;
                    if let Some(snapshot) = &workspace.snapshot {
                        let actual = format!("{:x}", Sha256::digest(content.as_bytes()));
                        if !snapshot
                            .manifest
                            .files
                            .iter()
                            .any(|f| f.path == request.path && f.sha256 == actual)
                        {
                            return Err(ReasonCode::SnapshotChanged);
                        }
                    }
                    data = json!({"bytes_read":content.len(),"sha256":format!("{:x}",Sha256::digest(content.as_bytes())),"content":content});
                    if let Some(id) = &task.snapshot_id {
                        data["snapshot_id"] = json!(id);
                    }
                }
                Tool::WriteFile => {
                    let content = request
                        .content
                        .as_deref()
                        .ok_or(ReasonCode::InvalidRequest)?;
                    let index = permit.write_grant()?;
                    let count = self.shared.files.write(&path, content)?;
                    event.executed = true;
                    // No operation that can ordinarily fail is placed between
                    // successful replacement and consumption under this lock.
                    if let Some(index) = index {
                        task.grants[index].consume()?;
                    }
                    task.updated_at = self.shared.clock.sample().utc;
                    data = match index {
                        Some(index) => {
                            json!({"bytes_written":count,"grant_id":task.grants[index].grant_id,"remaining_uses":task.grants[index].remaining_uses})
                        }
                        None => {
                            json!({"bytes_written":count,"grant_id":null,"remaining_uses":null})
                        }
                    };
                }
            }
            Ok(())
        })();
        let mut code = result.err().unwrap_or(ReasonCode::Ok);
        event.reason_code = code;
        event.duration_us = micros(ctx.started);
        event.decision_us = decision_us;
        if let Some(grant) = granted_index.and_then(|i| task.grants.get(i)) {
            apply_grant(&mut event, grant);
        } else if let Some(grant) = request
            .grant_id
            .as_ref()
            .and_then(|id| task.grants.iter().find(|g| g.grant_id == *id))
        {
            apply_grant(&mut event, grant);
        }
        let allowed = event.allowed;
        let executed = event.executed;
        if self.append(event).is_err() {
            code = ReasonCode::AuditUnavailable;
        }
        self.outcome(ctx, code, allowed, executed, data, decision_us)
    }

    fn management_denied(
        &self,
        ctx: &RequestContext,
        task: &Task,
        tool: &str,
        code: ReasonCode,
    ) -> Result<(), ReasonCode> {
        let mut event = Event::new("result", &ctx.request_id, self.shared.clock.sample());
        event.task_id = Some(task.task_id.clone());
        event.phase = Some(task.phase);
        event.tool = Some(tool.into());
        event.reason_code = code;
        event.duration_us = micros(ctx.started);
        self.append(event)
    }
}

fn summary(task: &Task) -> Value {
    json!({"task_id":task.task_id,"workspace_id":task.workspace_id,"snapshot_id":task.snapshot_id,"phase":task.phase,"permission_profile":task.profile,"goal":task.goal,"created_at":task.created_at,"updated_at":task.updated_at})
}

fn grant_snapshot(grant: &Grant, phase: Phase, now: TimePoint) -> Value {
    json!({"grant_id":grant.grant_id,"task_id":grant.task_id,"tool":grant.tool,"path":grant.path,"expires_at":grant.expires_at,
        "remaining_uses":grant.remaining_uses,"revoked":grant.revoked,"status":grant.status(phase,now.monotonic)})
}

fn apply_grant(event: &mut Event, grant: &Grant) {
    event.grant_id = Some(grant.grant_id.clone());
    event.remaining_uses = Some(grant.remaining_uses);
    event.expires_at = Some(grant.expires_at.to_rfc3339());
    event.revoked = Some(grant.revoked);
}
