//! Administrator imports and task-bound read-only metadata queries.

use super::*;

impl Broker {
    pub(super) fn workspace(&self, id: &str) -> Result<Arc<Workspace>, ReasonCode> {
        lock(&self.shared.workspaces)?
            .get(id)
            .cloned()
            .ok_or(ReasonCode::InvalidRequest)
    }

    pub(crate) fn preview_workspace(
        &self,
        ctx: &RequestContext,
        source: &Path,
    ) -> Result<Value, ReasonCode> {
        if !self.healthy() {
            return Err(ReasonCode::AuditUnavailable);
        }
        let scanned = workspace::scan(source)?;
        let now = self.shared.clock.sample();
        let id = format!(
            "preview_{}",
            random_hex::<16>().map_err(|_| ReasonCode::IoError)?
        );
        let preview = Preview {
            source: scanned.source,
            manifest: scanned.manifest,
            deadline: now.monotonic + PREVIEW_TTL,
            expires_at: now.utc + chrono::Duration::seconds(PREVIEW_TTL.as_secs() as i64),
        };
        let mut response = manifest_value(&preview.manifest);
        response["preview_id"] = json!(id);
        response["source_path"] = json!(preview.source);
        response["expires_at"] = json!(preview.expires_at);
        response["request_id"] = json!(ctx.request_id);
        response["instance_id"] = json!(self.instance_id());
        let mut previews = lock(&self.shared.previews)?;
        previews.retain(|_, p| p.deadline > now.monotonic);
        if previews.len() >= 16 {
            return Err(ReasonCode::ImportLimitExceeded);
        }
        let mut event = Event::new("workspace_previewed", &ctx.request_id, now);
        event.allowed = true;
        event.executed = true;
        event.reason_code = ReasonCode::Ok;
        event.duration_us = micros(ctx.started);
        self.append(event)?;
        previews.insert(id, preview);
        Ok(response)
    }

    pub(crate) fn preview_exclusions(
        &self,
        ctx: &RequestContext,
        id: &str,
        offset: usize,
        limit: usize,
    ) -> Result<Value, ReasonCode> {
        if !(1..=200).contains(&limit) {
            return Err(ReasonCode::InvalidRequest);
        }
        let now = self.shared.clock.sample();
        let mut previews = lock(&self.shared.previews)?;
        if previews
            .get(id)
            .is_none_or(|preview| preview.deadline <= now.monotonic)
        {
            previews.remove(id);
            return Err(ReasonCode::PreviewExpired);
        }
        let preview = previews.get(id).ok_or(ReasonCode::PreviewExpired)?;
        let total = preview.manifest.excluded.len();
        if offset > total {
            return Err(ReasonCode::InvalidRequest);
        }
        let end = offset.saturating_add(limit).min(total);
        let next_offset = (end < total).then_some(end);
        Ok(json!({
            "preview_id": id,
            "excluded": &preview.manifest.excluded[offset..end],
            "excluded_entry_count": total,
            "next_offset": next_offset,
            "expires_at": preview.expires_at,
            "request_id": ctx.request_id,
            "instance_id": self.instance_id(),
        }))
    }

    pub(crate) fn register_workspace(
        &self,
        ctx: &RequestContext,
        preview_id: &str,
        name: String,
    ) -> Result<Value, ReasonCode> {
        let now = self.shared.clock.sample();
        let preview = lock(&self.shared.previews)?
            .remove(preview_id)
            .ok_or(ReasonCode::PreviewExpired)?;
        if now.monotonic >= preview.deadline {
            return Err(ReasonCode::PreviewExpired);
        }
        if !self.healthy() {
            return Err(ReasonCode::AuditUnavailable);
        }
        let id = format!(
            "ws_{}",
            random_hex::<16>().map_err(|_| ReasonCode::IoError)?
        );
        let sid = format!(
            "snap_{}",
            random_hex::<16>().map_err(|_| ReasonCode::IoError)?
        );
        let workspace = Arc::new(workspace::import(preview, name, sid.clone(), now.utc)?);
        let mut registry = lock(&self.shared.workspaces)?;
        if registry.len() >= 32 {
            return Err(ReasonCode::ImportLimitExceeded);
        }
        let mut event = Event::new("workspace_registered", &ctx.request_id, now);
        event.workspace_id = Some(id.clone());
        event.snapshot_id = Some(sid);
        event.allowed = true;
        event.executed = true;
        event.reason_code = ReasonCode::Ok;
        event.duration_us = micros(ctx.started);
        self.append(event)?;
        let mut result = workspace_value(&id, &workspace);
        registry.insert(id, workspace);
        result["request_id"] = json!(ctx.request_id);
        result["instance_id"] = json!(self.instance_id());
        Ok(result)
    }

    pub(crate) fn list_workspaces(&self) -> Result<Value, ReasonCode> {
        let registry = lock(&self.shared.workspaces)?;
        let mut ids: Vec<_> = registry.keys().collect();
        ids.sort();
        let items: Vec<_> = ids
            .into_iter()
            .map(|id| workspace_summary_value(id, &registry[id]))
            .collect();
        Ok(json!({"items": items, "instance_id": self.instance_id()}))
    }

    fn agent_query<F>(
        &self,
        ctx: &RequestContext,
        id: &str,
        tool: &str,
        query: F,
    ) -> Result<Value, ReasonCode>
    where
        F: FnOnce(&Task, &Workspace) -> Result<Value, ReasonCode>,
    {
        if ctx.agent_task.as_deref() != Some(id) {
            return Err(self
                .reject(ctx, ReasonCode::TaskTokenMismatch, true)
                .reason_code);
        }
        let task = self.task(id)?;
        let task = lock(&task)?;
        let workspace = self.workspace(&task.workspace_id)?;
        let mut event = Event::new("result", &ctx.request_id, self.shared.clock.sample());
        event.task_id = Some(id.into());
        event.workspace_id = Some(task.workspace_id.clone());
        event.snapshot_id = task.snapshot_id.clone();
        event.phase = Some(task.phase);
        event.tool = Some(tool.into());
        let result = if self.healthy() {
            query(&task, &workspace)
        } else {
            Err(ReasonCode::AuditUnavailable)
        };
        event.allowed = result.is_ok();
        event.executed = result.is_ok();
        event.reason_code = result.as_ref().err().copied().unwrap_or(ReasonCode::Ok);
        event.duration_us = micros(ctx.started);
        self.append(event)?;
        let mut value = result?;
        value["request_id"] = json!(ctx.request_id);
        value["instance_id"] = json!(self.instance_id());
        Ok(value)
    }

    pub(crate) fn agent_status(&self, ctx: &RequestContext, id: &str) -> Result<Value, ReasonCode> {
        self.agent_query(ctx, id, "task_status", |task, _| {
            let mut value = summary(task);
            value["allowed_tools"] = if task.phase == Phase::Done {
                json!(["task_status"])
            } else if task.profile == PermissionProfile::Readonly {
                json!(["list_files", "read_file", "task_status"])
            } else {
                json!(["read_file", "write_file", "task_status"])
            };
            Ok(value)
        })
    }

    pub(crate) fn agent_files(
        &self,
        ctx: &RequestContext,
        id: &str,
        prefix: &str,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<Value, ReasonCode> {
        self.agent_query(ctx, id, "list_files", |task, workspace| {
            if task.phase == Phase::Done { return Err(ReasonCode::InvalidStage); }
            if !(1..=200).contains(&limit) || (!prefix.is_empty() && agentfence_core::validate_relative(prefix.trim_end_matches('/')).is_err()) { return Err(ReasonCode::InvalidRequest); }
            let snapshot = workspace.snapshot.as_ref().ok_or(ReasonCode::InvalidRequest)?;
            let files: Vec<_> = snapshot.manifest.files.iter().filter(|f| f.path.starts_with(prefix)).collect();
            let offset = match cursor {
                None => 0,
                Some(cursor) => {
                    if cursor.len() > 128 { return Err(ReasonCode::InvalidRequest); }
                    let (number, signature) = cursor.split_once(':').ok_or(ReasonCode::InvalidRequest)?;
                    let offset = number.parse::<usize>().map_err(|_| ReasonCode::InvalidRequest)?;
                    if signature.len() != 64 { return Err(ReasonCode::InvalidRequest); }
                    let bytes: Result<Vec<_>, _> = (0..64).step_by(2).map(|i| u8::from_str_radix(signature.get(i..i+2).ok_or(())?, 16).map_err(|_| ())).collect();
                    self.cursor_mac(task, prefix, offset)?.verify_slice(&bytes.map_err(|_| ReasonCode::InvalidRequest)?).map_err(|_| ReasonCode::InvalidRequest)?;
                    offset
                }
            };
            if offset > files.len() { return Err(ReasonCode::InvalidRequest); }
            let items: Vec<_> = files.iter().skip(offset).take(limit).collect();
            let end = offset + items.len();
            let next = if end < files.len() {
                let signature = self.cursor_mac(task, prefix, end)?.finalize().into_bytes();
                Some(format!("{end}:{}", signature.iter().map(|b| format!("{b:02x}")).collect::<String>()))
            } else { None };
            Ok(json!({"task_id":id, "snapshot_id":snapshot.id, "files":items, "total":files.len(), "next_cursor":next}))
        })
    }

    fn cursor_mac(
        &self,
        task: &Task,
        prefix: &str,
        offset: usize,
    ) -> Result<Hmac<Sha256>, ReasonCode> {
        let mut mac = Hmac::<Sha256>::new_from_slice(&self.shared.admin_hash)
            .map_err(|_| ReasonCode::IoError)?;
        let payload = serde_json::to_vec(&(
            self.instance_id(),
            &task.task_id,
            &task.snapshot_id,
            prefix,
            offset,
        ))
        .map_err(|_| ReasonCode::IoError)?;
        mac.update(&payload);
        Ok(mac)
    }
}

fn manifest_value(manifest: &workspace::Manifest) -> Value {
    json!({"files":manifest.files, "allowed_file_count":manifest.files.len(), "excluded_entry_count":manifest.excluded.len(), "excluded":manifest.excluded.iter().take(200).collect::<Vec<_>>(), "excluded_truncated":manifest.excluded.len()>200, "total_bytes":manifest.total_bytes, "manifest_sha256":manifest.sha256()})
}

fn workspace_value(id: &str, workspace: &Workspace) -> Value {
    if let Some(snapshot) = &workspace.snapshot {
        let mut value = manifest_value(&snapshot.manifest);
        value["workspace_id"] = json!(id);
        value["snapshot_id"] = json!(snapshot.id);
        value["display_name"] = json!(snapshot.display_name);
        value["source_path"] = json!(snapshot.source);
        value["imported_at"] = json!(snapshot.imported_at);
        value["import_mode"] = json!("readonly_snapshot");
        value
    } else {
        json!({"workspace_id":id, "snapshot_id":null, "display_name":id, "import_mode":"configured", "allowed_file_count":workspace.policy.allowed_paths().len()})
    }
}

fn workspace_summary_value(id: &str, workspace: &Workspace) -> Value {
    if let Some(snapshot) = &workspace.snapshot {
        json!({
            "workspace_id": id,
            "snapshot_id": snapshot.id,
            "display_name": snapshot.display_name,
            "source_path": snapshot.source,
            "imported_at": snapshot.imported_at,
            "import_mode": "readonly_snapshot",
            "allowed_file_count": snapshot.manifest.files.len(),
            "excluded_entry_count": snapshot.manifest.excluded.len(),
            "total_bytes": snapshot.manifest.total_bytes,
            "manifest_sha256": snapshot.manifest.sha256(),
        })
    } else {
        workspace_value(id, workspace)
    }
}
