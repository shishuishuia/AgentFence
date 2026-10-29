//! Administrator-only inspection and confirmed application of isolated edits.

use super::*;

impl Broker {
    fn edit_workspace(&self, id: &str) -> Result<(Arc<Mutex<Task>>, Arc<Workspace>), ReasonCode> {
        let task = self.task(id)?;
        let state = lock(&task)?;
        let workspace = self.workspace(&state.workspace_id)?;
        if state.profile != PermissionProfile::ControlledEdit
            || state.phase != Phase::Done
            || !workspace
                .snapshot
                .as_ref()
                .is_some_and(|snapshot| snapshot.editable)
        {
            return Err(ReasonCode::InvalidStage);
        }
        drop(state);
        Ok((task, workspace))
    }

    pub(crate) fn edit_changes(&self, id: &str) -> Result<Value, ReasonCode> {
        let (task, workspace) = self.edit_workspace(id)?;
        let _task = lock(&task)?;
        let _guard = lock(&workspace.gate)?;
        let snapshot = workspace
            .snapshot
            .as_ref()
            .ok_or(ReasonCode::InvalidRequest)?;
        let mut changes = Vec::new();
        for entry in &snapshot.manifest.files {
            let path = resolve_existing(&workspace.root, &entry.path).map_err(|e| e.reason())?;
            let content = self
                .shared
                .files
                .read(&path, workspace.policy.max_content_bytes())?;
            let after_sha = format!("{:x}", Sha256::digest(content.as_bytes()));
            if after_sha != entry.sha256 {
                changes.push(json!({"path":entry.path,"before_sha256":entry.sha256,"after_sha256":after_sha}));
            }
        }
        Ok(
            json!({"task_id":id,"snapshot_id":snapshot.id,"changes":changes,"instance_id":self.instance_id()}),
        )
    }

    pub(crate) fn edit_change(&self, id: &str, relative: &str) -> Result<Value, ReasonCode> {
        let (task, workspace) = self.edit_workspace(id)?;
        let _task = lock(&task)?;
        let _guard = lock(&workspace.gate)?;
        workspace.policy.check_path(relative)?;
        let snapshot = workspace
            .snapshot
            .as_ref()
            .ok_or(ReasonCode::InvalidRequest)?;
        let entry = snapshot
            .manifest
            .files
            .iter()
            .find(|f| f.path == relative)
            .ok_or(ReasonCode::PathDenied)?;
        let base = snapshot.base.as_ref().ok_or(ReasonCode::InvalidRequest)?;
        let before_path = resolve_existing(&base.root, relative).map_err(|e| e.reason())?;
        let after_path = resolve_existing(&workspace.root, relative).map_err(|e| e.reason())?;
        let before = self
            .shared
            .files
            .read(&before_path, workspace.policy.max_content_bytes())?;
        if format!("{:x}", Sha256::digest(before.as_bytes())) != entry.sha256 {
            return Err(ReasonCode::SnapshotChanged);
        }
        let after = self
            .shared
            .files
            .read(&after_path, workspace.policy.max_content_bytes())?;
        let after_hash = format!("{:x}", Sha256::digest(after.as_bytes()));
        if after_hash == entry.sha256 {
            return Err(ReasonCode::InvalidRequest);
        }
        Ok(
            json!({"task_id":id,"path":relative,"before":before,"after":after,"before_sha256":entry.sha256,"after_sha256":after_hash,"instance_id":self.instance_id()}),
        )
    }

    pub(crate) fn apply_edit(
        &self,
        ctx: &RequestContext,
        id: &str,
        relative: &str,
        expected_after: &str,
    ) -> Result<Value, ReasonCode> {
        let (task, workspace) = self.edit_workspace(id)?;
        let _task = lock(&task)?;
        let _guard = lock(&workspace.gate)?;
        if !self.healthy() {
            return Err(ReasonCode::AuditUnavailable);
        }
        let _apply_guard = lock(&self.shared.apply_gate)?;
        let snapshot = workspace
            .snapshot
            .as_ref()
            .ok_or(ReasonCode::InvalidRequest)?;
        let mut event = Event::new("intent", &ctx.request_id, self.shared.clock.sample());
        event.task_id = Some(id.into());
        event.workspace_id = Some(_task.workspace_id.clone());
        event.snapshot_id = Some(snapshot.id.clone());
        event.phase = Some(Phase::Done);
        event.tool = Some("apply_edit".into());
        event.path = Some(relative.chars().take(256).collect());
        event.reason_code = ReasonCode::Pending;
        self.append(event.clone())?;
        let result = (|| -> Result<String, ReasonCode> {
            workspace.policy.check_path(relative)?;
            let entry = snapshot
                .manifest
                .files
                .iter()
                .find(|f| f.path == relative)
                .ok_or(ReasonCode::PathDenied)?;
            let changed_path =
                resolve_existing(&workspace.root, relative).map_err(|e| e.reason())?;
            let changed = self
                .shared
                .files
                .read(&changed_path, workspace.policy.max_content_bytes())?;
            let after_sha = format!("{:x}", Sha256::digest(changed.as_bytes()));
            if after_sha != expected_after || after_sha == entry.sha256 {
                return Err(ReasonCode::SnapshotChanged);
            }
            let original_path =
                resolve_existing(&snapshot.source, relative).map_err(|e| e.reason())?;
            let original = self
                .shared
                .files
                .read(&original_path, workspace.policy.max_content_bytes())?;
            if format!("{:x}", Sha256::digest(original.as_bytes())) != entry.sha256 {
                return Err(ReasonCode::SnapshotChanged);
            }
            event.allowed = true;
            self.shared.files.write(&original_path, &changed)?;
            event.executed = true;
            Ok(after_sha)
        })();
        event.event_type = "result".into();
        event.reason_code = result.as_ref().err().copied().unwrap_or(ReasonCode::Ok);
        event.duration_us = micros(ctx.started);
        self.append(event)?;
        let after_sha = result?;
        Ok(
            json!({"task_id":id,"path":relative,"sha256":after_sha,"request_id":ctx.request_id,"instance_id":self.instance_id()}),
        )
    }
}
