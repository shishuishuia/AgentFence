import { ApiError, getJson } from './client'

export interface DemoStep {
  case: string
  http_status: number
  reason_code: string | null
  request_id: string
  allowed: boolean | null
  executed: boolean | null
  pass: boolean
}

export interface DemoState {
  enabled: boolean
  instance_id?: string
  run_id: string | null
  status: 'idle' | 'running' | 'paused' | 'succeeded' | 'failed'
  task_id: string | null
  phase: string | null
  completed_steps: number
  total_steps: number
  steps: DemoStep[]
  failure: string | null
  verified: boolean
}

function valid(value: DemoState): DemoState {
  if (
    !value ||
    value.enabled !== true ||
    typeof value.instance_id !== 'string' ||
    !['idle', 'running', 'paused', 'succeeded', 'failed'].includes(value.status) ||
    !Number.isInteger(value.completed_steps) ||
    value.total_steps !== 13 ||
    !Array.isArray(value.steps) ||
    value.steps.length !== value.completed_steps ||
    !value.steps.every(
      (s) =>
        s &&
        typeof s.case === 'string' &&
        typeof s.http_status === 'number' &&
        typeof s.request_id === 'string' &&
        typeof s.pass === 'boolean',
    )
  )
    throw new ApiError(502, 'INVALID_RESPONSE', '演示状态不符合接口约定。')
  return value
}

export async function getDemoState(signal?: AbortSignal): Promise<DemoState> {
  try {
    return valid(await getJson<DemoState>('/demo/state', signal))
  } catch (error) {
    if (error instanceof ApiError && error.status === 404)
      return {
        enabled: false,
        run_id: null,
        status: 'idle',
        task_id: null,
        phase: null,
        completed_steps: 0,
        total_steps: 13,
        steps: [],
        failure: null,
        verified: false,
      }
    throw error
  }
}

/** The only browser mutation: a fixed demo action, with no credentials or tool inputs. */
export async function controlDemo(
  action: 'start' | 'pause' | 'resume',
  requestId: string,
  signal: AbortSignal,
): Promise<DemoState> {
  const response = await fetch('/api/demo/control', {
    method: 'POST',
    credentials: 'omit',
    signal,
    headers: { 'Content-Type': 'application/json', 'X-AgentFence-Demo': '1' },
    body: JSON.stringify({ action, request_id: requestId }),
  })
  if (!response.ok && response.status !== 409)
    throw new Error(`演示控制失败（${response.status}），请刷新执行状态。`)
  const state = valid((await response.json()) as DemoState)
  if (response.status === 409) throw new Error('任务状态已变化或已有任务正在执行，请刷新执行状态。')
  return state
}
