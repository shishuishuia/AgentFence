import { ApiError } from './client'
import type { TaskSummary } from '@/types/api'

export interface Preview {
  files: { path: string; bytes: number; sha256: string }[]
  manifest_sha256: string
  expires_at: string
  allowed_file_count: number
  excluded_file_count: number
  total_bytes: number
  excluded: { path: string; reason_code: string }[]
}
export interface Selection {
  selection_id: string
  state: 'pending' | 'ready' | 'cancelled' | 'failed' | 'consumed'
  display_path?: string
  preview?: Preview
  reason_code?: string
  message?: string
}
export interface Workspace {
  workspace_id: string
  snapshot_id: string
  display_name: string
  import_mode: 'readonly_snapshot'
  imported_at: string
  manifest_sha256: string
  allowed_file_count: number
  excluded_file_count: number
  total_bytes: number
}
const base = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')
let csrf = ''
export async function management<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${base}/admin${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      signal,
      headers: {
        Accept: 'application/json',
        ...(body === undefined
          ? {}
          : { 'Content-Type': 'application/json', 'X-AgentFence-CSRF': csrf }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new ApiError(0, 'NETWORK_ERROR', '本机管理服务连接失败；操作未自动重试。')
  }
  let value: unknown
  try {
    value = await response.json()
  } catch {
    throw new ApiError(502, 'INVALID_RESPONSE', '管理服务未返回 JSON。')
  }
  if (!response.ok) {
    const v = value as Record<string, unknown> | null
    throw new ApiError(
      response.status,
      typeof v?.reason_code === 'string' ? v.reason_code : 'HTTP_ERROR',
      typeof v?.message === 'string' ? v.message : '管理操作失败。',
      typeof v?.request_id === 'string' ? v.request_id : undefined,
    )
  }
  return value as T
}
export async function openManagement(signal?: AbortSignal) {
  const value = await management<{ csrf_token: string }>('/session', {}, signal)
  if (typeof value?.csrf_token !== 'string' || !value.csrf_token)
    throw new ApiError(502, 'INVALID_RESPONSE', '管理会话无效。')
  csrf = value.csrf_token
}
const record = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === 'object' && !Array.isArray(v)
const counts = (v: Record<string, unknown>) =>
  ['allowed_file_count', 'excluded_file_count', 'total_bytes'].every(
    (k) => Number.isSafeInteger(v[k]) && Number(v[k]) >= 0,
  )
export function isWorkspace(v: unknown): v is Workspace {
  return (
    record(v) &&
    ['workspace_id', 'snapshot_id', 'display_name', 'imported_at', 'manifest_sha256'].every(
      (k) => typeof v[k] === 'string',
    ) &&
    v.import_mode === 'readonly_snapshot' &&
    counts(v)
  )
}
function checkedSelection(v: unknown): Selection {
  if (
    record(v) &&
    typeof v.selection_id === 'string' &&
    /^sel_[a-f0-9]{32}$/.test(v.selection_id) &&
    ['pending', 'ready', 'cancelled', 'failed', 'consumed'].includes(String(v.state))
  ) {
    if (
      v.state !== 'ready' ||
      (typeof v.display_path === 'string' &&
        record(v.preview) &&
        counts(v.preview) &&
        typeof v.preview.expires_at === 'string' &&
        typeof v.preview.manifest_sha256 === 'string' &&
        Array.isArray(v.preview.files) &&
        v.preview.files.every(
          (f) =>
            record(f) &&
            typeof f.path === 'string' &&
            Number.isSafeInteger(f.bytes) &&
            typeof f.sha256 === 'string',
        ) &&
        Array.isArray(v.preview.excluded) &&
        v.preview.excluded.every(
          (x) => record(x) && typeof x.path === 'string' && typeof x.reason_code === 'string',
        ))
    )
      return v as unknown as Selection
  }
  throw new ApiError(502, 'INVALID_RESPONSE', '目录预览响应无效。')
}
export const startPicker = async (signal?: AbortSignal) =>
  checkedSelection(await management('/picker/start', {}, signal))
export const getSelection = async (id: string, signal?: AbortSignal) =>
  checkedSelection(await management(`/picker/${encodeURIComponent(id)}`, undefined, signal))
export async function confirmWorkspace(id: string, name: string, signal?: AbortSignal) {
  const value = await management(
    '/workspaces/confirm',
    { selection_id: id, display_name: name, profile: 'readonly_snapshot' },
    signal,
  )
  if (!isWorkspace(value)) throw new ApiError(502, 'INVALID_RESPONSE', '工作区响应无效。')
  return value
}
export async function getWorkspaces(signal?: AbortSignal) {
  const value = await management('/workspaces', undefined, signal)
  if (!record(value) || !Array.isArray(value.items) || !value.items.every(isWorkspace))
    throw new ApiError(502, 'INVALID_RESPONSE', '工作区列表响应无效。')
  return { items: value.items }
}

export async function getExclusions(id: string, offset: number, signal?: AbortSignal) {
  const value = await management(
    `/picker/${encodeURIComponent(id)}/excluded?offset=${offset}`,
    undefined,
    signal,
  )
  if (
    !record(value) ||
    !Array.isArray(value.items) ||
    !value.items.every(
      (x) => record(x) && typeof x.path === 'string' && typeof x.reason_code === 'string',
    ) ||
    (value.next_offset !== null &&
      (!Number.isSafeInteger(value.next_offset) || Number(value.next_offset) <= offset))
  )
    throw new ApiError(502, 'INVALID_RESPONSE', '排除项分页响应无效。')
  return value as unknown as {
    items: { path: string; reason_code: string }[]
    next_offset: number | null
  }
}

export async function createReadonlyTask(workspaceId: string, goal: string, signal?: AbortSignal) {
  const value = await management(
    '/tasks',
    { workspace_id: workspaceId, goal, permission_profile: 'readonly' },
    signal,
  )
  if (
    !record(value) ||
    value.permission_profile !== 'readonly' ||
    value.phase !== 'inspect' ||
    value.workspace_id !== workspaceId ||
    typeof value.task_id !== 'string' ||
    typeof value.snapshot_id !== 'string' ||
    typeof value.goal !== 'string'
  )
    throw new ApiError(502, 'INVALID_RESPONSE', '服务端未确认只读任务，已停止后续操作。')
  return value as unknown as TaskSummary
}
