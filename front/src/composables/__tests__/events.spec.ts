import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAuditEvents } from '../useAuditEvents'
import type { RequestContext } from '../usePolling'
import { getEvents } from '@/api'
import { ApiError } from '@/api/client'

vi.mock('@/api', () => ({ getEvents: vi.fn<typeof getEvents>() }))
afterEach(() => vi.clearAllMocks())
const context = (): RequestContext => ({
  signal: new AbortController().signal,
  isCurrent: () => true,
  observe: vi.fn<(id: string | null | undefined) => void>(),
})
const page = (ids: number[], cursor: string, more = false) => ({
  instance_id: 'inst_1',
  events: ids.map((id) => ({ event_id: id, instance_id: 'inst_1' })),
  next_cursor: cursor,
  has_more: more,
})

describe('事件增量分页', () => {
  it('不存在的旧任务停止自动重试', () => {
    const feed = useAuditEvents()
    expect(feed.fatal(new ApiError(404, 'TASK_NOT_FOUND', 'task missing'))).toBe(true)
    expect(feed.fatal(new ApiError(502, 'BROKER_UNAVAILABLE', 'offline'))).toBe(false)
  })
  it('空页仍更新游标，跨页去重，重置后清空', async () => {
    vi.mocked(getEvents)
      .mockResolvedValueOnce(page([1], 'opaque-a', true) as never)
      .mockResolvedValueOnce(page([1, 2], 'opaque-b') as never)
      .mockResolvedValueOnce(page([], 'opaque-c') as never)
      .mockResolvedValueOnce(page([], 'opaque-d') as never)
    const feed = useAuditEvents()
    await feed.load(context(), 'task_1')
    expect(feed.events.value.map((e) => e.event_id)).toEqual([1, 2])
    await feed.load(context(), 'task_1')
    await feed.load(context(), 'task_1')
    expect(getEvents).toHaveBeenLastCalledWith(
      expect.objectContaining({ after: 'opaque-c' }),
      expect.any(AbortSignal),
    )
    feed.reset()
    expect(feed.events.value).toEqual([])
  })
  it('过期响应不能写入，重复游标不会无限翻页', async () => {
    vi.mocked(getEvents).mockResolvedValue(page([1], 'same', true) as never)
    const feed = useAuditEvents()
    await feed.load({ ...context(), isCurrent: () => false })
    expect(feed.events.value).toEqual([])
    await expect(feed.load(context())).rejects.toMatchObject({ reasonCode: 'INVALID_RESPONSE' })
    expect(getEvents).toHaveBeenCalledTimes(3)
  })
})
