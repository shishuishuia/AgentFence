import { describe, expect, it } from 'vitest'
import type { AuditEvent } from '@/types/api'
import { classify, pendingRequests, statistics } from '../audit'

const event = (values: Partial<AuditEvent> = {}) =>
  ({
    instance_id: 'inst_1',
    request_id: 'req_1',
    event_type: 'result',
    reason_code: 'OK',
    allowed: true,
    executed: true,
    ...values,
  }) as AuditEvent

describe('审计结果分类', () => {
  it('只读拒绝和任务身份拒绝计入拦截', () => {
    expect(
      statistics([
        event({ reason_code: 'READONLY_TASK', allowed: false, executed: false }),
        event({ reason_code: 'TASK_TOKEN_MISMATCH', allowed: false, executed: false }),
      ]),
    ).toEqual({ allowed: 0, denied: 2, failed: 0 })
  })
  it('只统计 result，区分策略拒绝与输入及执行失败', () => {
    const events = [
      event(),
      event({ reason_code: 'LEASE_USED_UP', executed: false }),
      event({ reason_code: 'INVALID_TOKEN', executed: false }),
      event({ reason_code: 'IO_ERROR', allowed: true, executed: false }),
      event({ reason_code: 'AUDIT_UNAVAILABLE', executed: true }),
      event({ event_type: 'task_created' }),
      event({ event_type: 'intent' }),
    ]
    expect(statistics(events)).toEqual({ allowed: 1, denied: 1, failed: 3 })
    expect(classify(events[5]!)).toBeNull()
  })
  it('按实例与请求关联 intent，未确认不算失败', () => {
    expect(pendingRequests([event({ event_type: 'intent' }), event()]).size).toBe(0)
    expect(
      pendingRequests([event({ event_type: 'intent' }), event({ instance_id: 'inst_2' })]).size,
    ).toBe(1)
  })
})
