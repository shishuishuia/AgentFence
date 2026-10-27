import { ref, shallowRef } from 'vue'
import { getEvents } from '@/api'
import { ApiError } from '@/api/client'
import type { AuditEvent } from '@/types/api'
import type { RequestContext } from './usePolling'

/** Pagination state only; the containing resource owns the polling timer. */
export function useAuditEvents() {
  const events = shallowRef<AuditEvent[]>([])
  const truncated = ref(false)
  const hasMore = ref(false)
  let cursor: string | undefined

  function reset() {
    events.value = []
    cursor = undefined
    truncated.value = false
    hasMore.value = false
  }

  async function load(context: RequestContext, taskId?: string) {
    for (let page = 0; page < 5; page++) {
      const response = await getEvents(
        { task_id: taskId, after: cursor, limit: 100 },
        context.signal,
      )
      if (!context.isCurrent()) return
      context.observe(response.instance_id)
      if (!context.isCurrent()) return
      if (response.has_more && response.next_cursor === cursor)
        throw new ApiError(502, 'INVALID_RESPONSE', '事件游标未推进，请重新加载。')
      const merged = new Map(
        events.value.map((event) => [`${event.instance_id}:${event.event_id}`, event]),
      )
      for (const event of response.events)
        merged.set(`${event.instance_id}:${event.event_id}`, event)
      const all = [...merged.values()].sort((a, b) => a.event_id - b.event_id)
      if (all.length > 2000) truncated.value = true
      events.value = all.slice(-2000)
      cursor = response.next_cursor
      hasMore.value = response.has_more
      if (!response.has_more) break
    }
    return { count: events.value.length }
  }

  const fatal = (error: Error) =>
    error instanceof ApiError &&
    (error.reasonCode === 'INVALID_REQUEST' || error.reasonCode === 'INVALID_RESPONSE')
  return { events, truncated, hasMore, reset, load, fatal }
}
