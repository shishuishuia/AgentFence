import { getJson } from './client'
import type {
  EventListResponse,
  HealthResponse,
  TaskDetailResponse,
  TaskListResponse,
} from '@/types/api'

export const getHealth = (signal?: AbortSignal) => getJson<HealthResponse>('/health', signal)

export const getTasks = (limit = 50, signal?: AbortSignal) =>
  getJson<TaskListResponse>(`/tasks?${new URLSearchParams({ limit: String(limit) })}`, signal)

export const getTask = (taskId: string, signal?: AbortSignal) =>
  getJson<TaskDetailResponse>(`/tasks/${encodeURIComponent(taskId)}`, signal)

export function getEvents(
  query: { task_id?: string; after?: string; limit?: number } = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams()
  if (query.task_id) params.set('task_id', query.task_id)
  if (query.after) params.set('after', query.after)
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  const suffix = params.size ? `?${params}` : ''
  return getJson<EventListResponse>(`/events${suffix}`, signal)
}
