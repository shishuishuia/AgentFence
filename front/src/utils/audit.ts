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
])

export function classify(event: AuditEvent): Exclude<ResultFilter, 'all'> | null {
  if (event.event_type !== 'result') return null
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
