//! Serialized JSONL writes with a latched fail-closed state.

use agentfence_core::{Phase, ReasonCode, TimePoint};
use serde::Serialize;
use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Seek, SeekFrom, Write},
    path::Path,
};

/// One event. Optional resource/state fields are JSON null when unknown.
#[derive(Debug, Clone, Serialize)]
pub struct Event {
    /// Monotonically increasing cursor within this instance.
    pub event_id: u64,
    /// Random server startup identity; cursors do not span instances.
    pub instance_id: String,
    /// Correlates intent/result or an administrator mutation with its response.
    pub request_id: String,
    /// Owning task, or null if untrusted/unknown.
    pub task_id: Option<String>,
    /// Registered workspace identity, when known; never an absolute root.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workspace_id: Option<String>,
    /// Immutable imported version, when this operation uses a snapshot.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub snapshot_id: Option<String>,
    /// Server UTC RFC3339 timestamp.
    pub timestamp: String,
    /// Phase observed within the task execution lock.
    pub phase: Option<Phase>,
    /// intent, result, task_created, phase_changed, grant_issued or grant_revoked.
    pub event_type: String,
    /// Tool name, or null before request validation.
    pub tool: Option<String>,
    /// Bounded relative/attempted path; JSON serialization escapes controls.
    pub path: Option<String>,
    /// Validated grant ID or null.
    pub grant_id: Option<String>,
    /// Whether the complete policy allowed execution; false for pending intents.
    pub allowed: bool,
    /// Whether the requested file operation completed.
    pub executed: bool,
    /// Stable decision/failure code; PENDING for an unconfirmed intent.
    pub reason_code: ReasonCode,
    /// Elapsed server microseconds sampled before appending this event.
    pub duration_us: u64,
    /// Pure-policy microseconds, excluding filesystem metadata and logging.
    pub decision_us: u64,
    /// Lease snapshot after this operation, if applicable.
    pub remaining_uses: Option<u8>,
    /// Display-only UTC lease deadline.
    pub expires_at: Option<String>,
    /// Lease revocation snapshot, if applicable.
    pub revoked: Option<bool>,
}

impl Event {
    pub(crate) fn new(kind: &str, request_id: &str, now: TimePoint) -> Self {
        Self {
            event_id: 0,
            instance_id: String::new(),
            request_id: request_id.into(),
            task_id: None,
            workspace_id: None,
            snapshot_id: None,
            timestamp: now.utc.to_rfc3339(),
            phase: None,
            event_type: kind.into(),
            tool: None,
            path: None,
            grant_id: None,
            allowed: false,
            executed: false,
            reason_code: ReasonCode::Pending,
            duration_us: 0,
            decision_us: 0,
            remaining_uses: None,
            expires_at: None,
            revoked: None,
        }
    }
}

/// A trusted append-only sink. Production uses FileSink; tests inject failures.
pub trait AuditSink: Send {
    /// Confirm one complete JSON line including its newline, or return failure.
    fn append(&mut self, line: &[u8]) -> io::Result<()>;
}

/// JSONL file sink, opened once outside every controlled workspace.
pub struct FileSink {
    file: File,
}

impl FileSink {
    /// Open/create an appendable file and check a nonempty file's line boundary.
    ///
    /// A partial trailing line is rejected rather than silently corrupting the
    /// next instance's JSONL. This does not validate or sign historical records.
    pub fn open(path: &Path) -> io::Result<Self> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .read(true)
            .open(path)?;
        if file.metadata()?.len() > 0 {
            file.seek(SeekFrom::End(-1))?;
            let mut last = [0];
            file.read_exact(&mut last)?;
            if last[0] != b'\n' {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "audit file has an incomplete trailing line",
                ));
            }
        }
        file.sync_data()?;
        Ok(Self { file })
    }
}

impl AuditSink for FileSink {
    fn append(&mut self, line: &[u8]) -> io::Result<()> {
        self.file.write_all(line)?;
        self.file.sync_data()
    }
}

pub(crate) struct AuditStore {
    sink: Box<dyn AuditSink>,
    instance_id: String,
    pub(crate) available: bool,
    pub(crate) events: Vec<Event>,
}

impl AuditStore {
    pub(crate) fn new(instance_id: String, sink: Box<dyn AuditSink>) -> Self {
        Self {
            sink,
            instance_id,
            available: true,
            events: Vec::new(),
        }
    }

    pub(crate) fn append(&mut self, mut event: Event) -> Result<(), ReasonCode> {
        if !self.available {
            return Err(ReasonCode::AuditUnavailable);
        }
        event.event_id = self.events.len() as u64 + 1;
        event.instance_id.clone_from(&self.instance_id);
        let result = (|| {
            let mut bytes = serde_json::to_vec(&event).map_err(io::Error::other)?;
            bytes.push(b'\n');
            self.sink.append(&bytes)
        })();
        match result {
            Ok(()) => {
                self.events.push(event);
                Ok(())
            }
            Err(_) => {
                self.available = false;
                Err(ReasonCode::AuditUnavailable)
            }
        }
    }
}
