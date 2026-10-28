export type Phase = 'inspect' | 'edit' | 'verify' | 'done'
export type GrantStatus = 'stage_inactive' | 'revoked' | 'expired' | 'used_up' | 'active'

export interface ErrorResponse {
  request_id: string
  instance_id: string | null
  reason_code: string
  message: string
}

export interface HealthResponse {
  instance_id: string
  status: 'ok'
}

export interface TaskSummary {
  task_id: string
  workspace_id: string
  phase: Phase
  created_at: string
  updated_at: string
  permission_profile?: 'readonly' | 'controlled_edit'
  snapshot_id?: string
  goal?: string
}

export interface Grant {
  grant_id: string
  task_id: string
  tool: 'write_file'
  path: string
  expires_at: string
  remaining_uses: number
  revoked: boolean
  status: GrantStatus
}

export interface TaskListResponse {
  instance_id: string
  items: TaskSummary[]
  total: number
  truncated: boolean
}

export interface TaskDetailResponse extends TaskSummary {
  instance_id: string
  server_time: string
  allowed_paths: string[]
  grants: Grant[]
}

export interface AuditEvent {
  event_id: number
  instance_id: string
  request_id: string
  task_id: string | null
  timestamp: string
  event_type:
    | 'task_created'
    | 'phase_changed'
    | 'grant_issued'
    | 'grant_revoked'
    | 'intent'
    | 'result'
    | 'management_denied'
    | 'workspace_registered'
    | 'snapshot_created'
  phase: Phase | null
  tool: string | null
  path: string | null
  grant_id: string | null
  allowed: boolean
  executed: boolean
  reason_code: string
  duration_us: number
  decision_us: number
  remaining_uses: number | null
  expires_at: string | null
  revoked: boolean | null
}

export interface EventListResponse {
  instance_id: string
  events: AuditEvent[]
  next_cursor: string
  has_more: boolean
}
