export type Phase = 'inspect' | 'edit' | 'verify' | 'done'
export type GrantStatus = 'stage_inactive' | 'revoked' | 'expired' | 'used_up' | 'active'

export interface ErrorResponse {
  request_id: string
  instance_id: string | null
  reason_code: string
  message: string
}

export interface HealthResponse {
  request_id: string
  instance_id: string
  status: 'ok' | 'degraded'
  audit_available: boolean
  reason_code: string
  message: string
}

export interface TaskSummary {
  task_id: string
  workspace_id: string
  phase: Phase
  created_at: string
  updated_at: string
}

export interface Grant {
  grant_id: string
  task_id: string
  tool: 'write_file'
  path: string
  created_at: string
  expires_at: string
  ttl_seconds: number
  max_uses: number
  remaining_uses: number
  revoked: boolean
  status: GrantStatus
}

export interface TaskListResponse {
  request_id: string
  instance_id: string
  items: TaskSummary[]
  total: number
  truncated: boolean
}

export interface TaskDetailResponse extends TaskSummary {
  request_id: string
  instance_id: string
  server_time: string
  allowed_paths: string[]
  grants: Grant[]
}

export interface AuditEvent {
  event_id: string
  instance_id: string
  request_id: string
  task_id: string | null
  timestamp: string
  event_type:
    | 'task_created'
    | 'phase_changed'
    | 'grant_issued'
    | 'grant_revoked'
    | 'tool_intent'
    | 'tool_result'
  phase: Phase | null
  tool: string | null
  path: string | null
  grant_id: string | null
  allowed: boolean | null
  executed: boolean | null
  reason_code: string | null
  duration_us: number | null
  decision_us: number | null
  remaining_uses: number | null
  expires_at: string | null
  revoked: boolean | null
  from_phase: Phase | null
  to_phase: Phase | null
}

export interface EventListResponse {
  request_id: string
  instance_id: string
  events: AuditEvent[]
  next_cursor: string
  has_more: boolean
}
