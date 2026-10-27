import { onScopeDispose, ref, shallowRef, watch, type MaybeRefOrGetter, toValue } from 'vue'
import { ApiError } from '@/api/client'
import { SessionChanged, useBrokerSession } from './useBrokerSession'

export interface RequestContext {
  signal: AbortSignal
  isCurrent: () => boolean
  observe: (id: string | null | undefined) => void
}

/** One owner per resource: completion-based polling and generation-checked writes. */
export function usePolling<T>(
  load: (context: RequestContext) => Promise<T>,
  options: { key?: MaybeRefOrGetter<string>; reset?: () => void; fatal?: (error: Error) => boolean; interval?: number } = {},
) {
  const session = useBrokerSession()
  const data = shallowRef<T | null>(null)
  const error = shallowRef<Error | null>(null)
  const loading = ref(false)
  const updatedAt = ref<string | null>(null)
  let timer: ReturnType<typeof setTimeout> | undefined
  let controller: AbortController | undefined
  let generation = 0
  let disposed = false

  function invalidate() {
    generation++
    clearTimeout(timer)
    controller?.abort()
  }

  async function refresh() {
    if (disposed) return
    invalidate()
    const ticket = generation
    const epoch = session.epoch.value
    const current = () => !disposed && ticket === generation && epoch === session.epoch.value
    const active = new AbortController()
    controller = active
    let timedOut = false
    const timeout = setTimeout(() => { timedOut = true; active.abort() }, 6000)
    const context: RequestContext = {
      signal: active.signal,
      isCurrent: () => current() && !active.signal.aborted,
      observe: (id) => { if (current()) session.observe(id, epoch) },
    }
    loading.value = true
    let fatal = false
    try {
      const result = await load(context)
      if (!context.isCurrent()) return
      if (result && typeof result === 'object' && 'instance_id' in result && typeof result.instance_id === 'string') context.observe(result.instance_id)
      if (!context.isCurrent()) return
      data.value = result
      error.value = null
      updatedAt.value = new Date().toISOString()
    } catch (caught) {
      if (!current() || caught instanceof SessionChanged) return
      if (caught instanceof ApiError) {
        try { context.observe(caught.instanceId) } catch (changed) { if (changed instanceof SessionChanged) return; throw changed }
      }
      if (!current() || (!timedOut && active.signal.aborted)) return
      const failure = timedOut ? new ApiError(0, 'REQUEST_TIMEOUT', '请求超时，请检查本地服务。') : caught instanceof Error ? caught : new Error('查询失败')
      error.value = failure
      fatal = options.fatal?.(failure) ?? false
    } finally {
      clearTimeout(timeout)
      if (current()) {
        loading.value = false
        if (!fatal) timer = setTimeout(() => { void refresh() }, options.interval ?? 1000)
      }
    }
  }

  function reset() {
    invalidate()
    data.value = null
    error.value = null
    updatedAt.value = null
    options.reset?.()
    void refresh()
  }

  watch([() => toValue(options.key ?? ''), session.epoch], reset, { immediate: true, flush: 'sync' })
  onScopeDispose(() => { disposed = true; invalidate() })
  return { data, error, loading, updatedAt, refresh, reset }
}
