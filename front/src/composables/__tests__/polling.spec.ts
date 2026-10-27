import { effectScope, ref, type EffectScope } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePolling, type RequestContext } from '../usePolling'
import { SessionChanged, useBrokerSession } from '../useBrokerSession'
import { ApiError } from '@/api/client'

let scope: EffectScope
beforeEach(() => {
  vi.useFakeTimers()
  scope = effectScope()
  const session = useBrokerSession()
  try { session.observe('inst_test', session.epoch.value) } catch (error) { if (!(error instanceof SessionChanged)) throw error }
})
afterEach(() => { scope.stop(); vi.useRealTimers() })

describe('请求生命周期', () => {
  it('请求超时展示错误，主动离开造成的取消不报失败', async () => {
    const load = (context: RequestContext) => new Promise<string>((_resolve, reject) => {
      context.signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')))
    })
    const state = scope.run(() => usePolling(load))!
    await vi.advanceTimersByTimeAsync(6000)
    expect(state.error.value).toMatchObject({ reasonCode: 'REQUEST_TIMEOUT' })
    expect(state.loading.value).toBe(false)
    scope.stop()
  })
  it('等待上一轮结束后才安排下一轮，并在离开时取消', async () => {
    let resolve!: (value: string) => void
    const load = vi.fn<(context: RequestContext) => Promise<string>>(() => new Promise<string>(done => { resolve = done }))
    scope.run(() => usePolling(load))
    await vi.advanceTimersByTimeAsync(3000)
    expect(load).toHaveBeenCalledTimes(1)
    resolve('first')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1000)
    expect(load).toHaveBeenCalledTimes(2)
    const signal = load.mock.calls[1]![0]?.signal
    scope.stop()
    expect(signal?.aborted).toBe(true)
    await vi.advanceTimersByTimeAsync(5000)
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('切换任务后旧响应和旧 finally 不覆盖新结果', async () => {
    const key = ref('A')
    const resolves: ((value: string) => void)[] = []
    const state = scope.run(() => usePolling(() => new Promise<string>(resolve => resolves.push(resolve)), { key }))!
    key.value = 'B'
    resolves[1]!('B result')
    await flushPromises()
    resolves[0]!('A late')
    await flushPromises()
    expect(state.data.value).toBe('B result')
    expect(state.error.value).toBeNull()
    expect(state.loading.value).toBe(false)
  })

  it('手动刷新取消旧请求，旧错误不会覆盖新数据', async () => {
    let reject!: (error: Error) => void
    const load = vi.fn<(context: RequestContext) => Promise<unknown>>().mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail })).mockResolvedValue('fresh')
    const state = scope.run(() => usePolling(load))!
    await state.refresh()
    reject(new Error('old failure'))
    await flushPromises()
    expect(state.data.value).toBe('fresh')
    expect(state.error.value).toBeNull()
    expect(load.mock.calls[0]![0].signal.aborted).toBe(true)
  })

  it('刷新失败保留已有数据与最后成功时间', async () => {
    const load = vi.fn<(context: RequestContext) => Promise<unknown>>().mockResolvedValueOnce('cached').mockRejectedValue(new Error('disconnected'))
    const state = scope.run(() => usePolling(load))!
    await flushPromises()
    const updated = state.updatedAt.value
    await state.refresh()
    expect(state.data.value).toBe('cached')
    expect(state.updatedAt.value).toBe(updated)
    expect(state.error.value?.message).toBe('disconnected')
  })

  it('新实例错误触发清空与重载，旧 epoch 无法切回', async () => {
    const session = useBrokerSession()
    const oldEpoch = session.epoch.value
    let reject!: (error: Error) => void
    const load = vi.fn<(context: RequestContext) => Promise<unknown>>().mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail })).mockResolvedValue({ instance_id: 'inst_new' })
    const reset = vi.fn<() => void>()
    const state = scope.run(() => usePolling(load, { reset }))!
    reject(new ApiError(400, 'INVALID_REQUEST', 'old cursor', 'req_1', 'inst_new'))
    await flushPromises()
    expect(session.instanceId.value).toBe('inst_new')
    expect(state.data.value).toEqual({ instance_id: 'inst_new' })
    expect(() => session.observe('inst_test', oldEpoch)).toThrow(SessionChanged)
    expect(session.instanceId.value).toBe('inst_new')
    expect(reset).toHaveBeenCalledTimes(2)
  })

  it('同实例非法游标停止自动重试，手动重载仍可用', async () => {
    const load = vi.fn<(context: RequestContext) => Promise<unknown>>().mockRejectedValue(new ApiError(400, 'INVALID_REQUEST', 'bad cursor', undefined, 'inst_test'))
    const state = scope.run(() => usePolling(load, { fatal: () => true }))!
    await flushPromises()
    await vi.advanceTimersByTimeAsync(4000)
    expect(load).toHaveBeenCalledTimes(1)
    state.reset()
    await flushPromises()
    expect(load).toHaveBeenCalledTimes(2)
  })
})
