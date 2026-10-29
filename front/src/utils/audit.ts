import type { AuditEvent } from '@/types/api'

export type ResultFilter = 'all' | 'allowed' | 'denied' | 'failed'
const denied = new Set([
  'INVALID_STAGE',
  'PATH_DENIED',
  'LEASE_REQUIRED',
  'LEASE_NOT_FOUND',
  'LEASE_MISMATCH',
  'LEASE_REVOKED',
  'LEASE_EXPIRED',
  'LEASE_USED_UP',
  'READONLY_TASK',
  'TASK_TOKEN_MISMATCH',
  'ROLE_DENIED',
])

export function classify(event: AuditEvent): Exclude<ResultFilter, 'all'> | null {
  if (event.event_type !== 'result' && event.event_type !== 'management_denied') return null
  if (event.reason_code === 'OK' && event.executed) return 'allowed'
  return denied.has(event.reason_code) ? 'denied' : 'failed'
}

export function statistics(events: AuditEvent[]) {
  const counts = { allowed: 0, denied: 0, failed: 0 }
  for (const event of events) {
    const result = classify(event)
    if (result) counts[result]++
  }
  return counts
}

export function pendingRequests(events: AuditEvent[]) {
  const completed = new Set(
    events.filter((e) => e.event_type === 'result').map((e) => `${e.instance_id}:${e.request_id}`),
  )
  return new Set(
    events
      .filter(
        (e) => e.event_type === 'intent' && !completed.has(`${e.instance_id}:${e.request_id}`),
      )
      .map((e) => `${e.instance_id}:${e.request_id}`),
  )
}

export function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

export function shortId(value: string | null | undefined, head = 8) {
  if (!value) return '—'
  return value.length <= head + 5 ? value : `${value.slice(0, head)}…`
}

const EVENT_LABELS: Record<string, string> = {
  intent: '调用前',
  result: '执行结果',
  task_created: '任务创建',
  phase_changed: '阶段变更',
  grant_issued: '租约签发',
  grant_revoked: '租约撤销',
  workspace_previewed: '工作区预览',
  workspace_registered: '工作区注册',
  edit_copy_created: '创建隔离副本',
  management_denied: '管理请求被拒',
}
export const eventLabel = (value: string) => EVENT_LABELS[value] ?? value

const PHASE_LABELS: Record<string, string> = {
  inspect: '检查',
  edit: '修改',
  verify: '验证',
  done: '完成',
}
export const phaseLabel = (value: string | null | undefined) =>
  value ? (PHASE_LABELS[value] ?? value) : '—'

const TOOL_LABELS: Record<string, string> = {
  read_file: '读取文件',
  write_file: '写入文件',
  list_files: '列出文件',
  task_status: '任务状态',
}
export const toolLabel = (value: string | null | undefined) =>
  value ? (TOOL_LABELS[value] ?? value) : '管理操作'

const REASON_LABELS: Record<string, string> = {
  OK: '正常',
  PENDING: '待确认',
  INVALID_STAGE: '当前阶段不允许',
  PATH_DENIED: '路径未授权',
  LEASE_REQUIRED: '缺少写租约',
  LEASE_NOT_FOUND: '租约不存在',
  LEASE_MISMATCH: '租约与路径不符',
  LEASE_REVOKED: '租约已撤销',
  LEASE_EXPIRED: '租约已过期',
  LEASE_USED_UP: '租约次数用尽',
  READONLY_TASK: '只读任务禁止写入',
  TASK_TOKEN_MISMATCH: '任务身份不匹配',
  ROLE_DENIED: '角色无权访问',
  INVALID_REQUEST: '请求参数无效',
  FILE_NOT_FOUND: '文件不存在',
  PAYLOAD_TOO_LARGE: '内容超出上限',
  NON_UTF8_CONTENT: '文件不是 UTF-8',
  IO_ERROR: '文件操作失败',
  AUDIT_UNAVAILABLE: '审计不可用',
  INVALID_TRANSITION: '阶段不能这样跳转',
  GRANT_CONFLICT: '已有可用租约',
  INVALID_TOKEN: '身份无效',
  UNKNOWN_TOOL: '不支持的工具',
  NOT_FOUND: '接口不存在',
  METHOD_NOT_ALLOWED: '方法不允许',
}
export const reasonLabel = (value: string | null | undefined) =>
  value ? (REASON_LABELS[value] ?? value) : '—'

/** Request IDs are only distinguishable by their per-instance sequence tail. */
export function shortRequest(value: string | null | undefined) {
  if (!value) return '—'
  const index = value.lastIndexOf('_')
  return index > 0 ? `…${value.slice(index)}` : value
}
