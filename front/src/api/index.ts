import { getJson } from './client'
import { ApiError } from './client'
import type {
  EventListResponse,
  HealthResponse,
  TaskDetailResponse,
  TaskListResponse,
} from '@/types/api'

type RecordValue = Record<string, unknown>
const record = (v: unknown): v is RecordValue => !!v && typeof v === 'object' && !Array.isArray(v)
const strings = (v: RecordValue, keys: string[]) => keys.every((key) => typeof v[key] === 'string')
const task = (v: unknown) =>
  record(v) &&
  strings(v, ['task_id', 'workspace_id', 'created_at', 'updated_at']) &&
  ['inspect', 'edit', 'verify', 'done'].includes(String(v.phase))
async function checked<T>(
  path: string,
  valid: (value: RecordValue) => boolean,
  signal?: AbortSignal,
): Promise<T> {
  const value = await getJson<unknown>(path, signal)
  if (!record(value) || typeof value.instance_id !== 'string' || !valid(value))
    throw new ApiError(502, 'INVALID_RESPONSE', '查询响应不符合接口约定。')
  return value as T
}

export const getHealth = (signal?: AbortSignal) =>
  checked<HealthResponse>('/health', (v) => v.status === 'ok', signal)

export const getTasks = (limit = 50, signal?: AbortSignal) =>
  checked<TaskListResponse>(
    `/tasks?${new URLSearchParams({ limit: String(limit) })}`,
    (v) =>
      Array.isArray(v.items) &&
      v.items.every(task) &&
      typeof v.total === 'number' &&
      typeof v.truncated === 'boolean',
    signal,
  )

export const getTask = (taskId: string, signal?: AbortSignal) =>
  checked<TaskDetailResponse>(
    `/tasks/${encodeURIComponent(taskId)}`,
    (v) =>
      task(v) &&
      typeof v.server_time === 'string' &&
      Array.isArray(v.allowed_paths) &&
      v.allowed_paths.every((p) => typeof p === 'string') &&
      Array.isArray(v.grants) &&
      v.grants.every(
        (g) =>
          record(g) &&
          strings(g, ['grant_id', 'task_id', 'tool', 'path', 'expires_at']) &&
          typeof g.remaining_uses === 'number' &&
          typeof g.revoked === 'boolean' &&
          ['stage_inactive', 'revoked', 'expired', 'used_up', 'active'].includes(String(g.status)),
      ),
    signal,
  )

export function getEvents(
  query: { task_id?: string; after?: string; limit?: number } = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams()
  if (query.task_id) params.set('task_id', query.task_id)
  if (query.after) params.set('after', query.after)
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  const suffix = params.size ? `?${params}` : ''
  return checked<EventListResponse>(
    `/events${suffix}`,
    (v) =>
      Array.isArray(v.events) &&
      v.events.every(
        (e) =>
          record(e) &&
          strings(e, ['instance_id', 'request_id', 'timestamp', 'event_type', 'reason_code']) &&
          typeof e.event_id === 'number' &&
          typeof e.allowed === 'boolean' &&
          typeof e.executed === 'boolean',
      ) &&
      typeof v.next_cursor === 'string' &&
      typeof v.has_more === 'boolean',
    signal,
  )
}
