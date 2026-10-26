import type { ErrorResponse } from '@/types/api'

const baseUrl = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly reasonCode: string,
    message: string,
    public readonly requestId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function isErrorResponse(value: unknown): value is ErrorResponse {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Record<string, unknown>
  return (
    typeof candidate.reason_code === 'string' &&
    typeof candidate.message === 'string' &&
    typeof candidate.request_id === 'string'
  )
}

/** 只调用同源只读代理，不注入管理员令牌，也不自动重试。 */
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    signal,
  })
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new ApiError(response.status, 'INVALID_RESPONSE', '接口未返回有效 JSON，请检查只读代理。')
  }
  if (!response.ok) {
    if (isErrorResponse(payload)) {
      throw new ApiError(response.status, payload.reason_code, payload.message, payload.request_id)
    }
    throw new ApiError(response.status, 'HTTP_ERROR', `接口请求失败（${response.status}）`)
  }
  return payload as T
}
