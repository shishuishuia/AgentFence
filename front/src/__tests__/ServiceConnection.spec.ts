import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ServiceConnection from '@/components/ServiceConnection.vue'

afterEach(() => vi.unstubAllGlobals())

describe('侧栏服务连接', () => {
  it('健康响应更新连接状态和实例，并允许手动刷新', async () => {
    const fetchHealth = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok', instance_id: 'inst_sidebar' })),
    )
    vi.stubGlobal('fetch', fetchHealth)
    const wrapper = mount(ServiceConnection)
    try {
      await flushPromises()
      expect(fetchHealth).toHaveBeenCalledWith('/api/health', expect.objectContaining({ method: 'GET' }))
      expect(wrapper.get('.status-label').text()).toBe('已连接')
      expect(wrapper.text()).toContain('inst_sidebar')
      await wrapper.get('button').trigger('click')
      await flushPromises()
      expect(fetchHealth).toHaveBeenCalledTimes(2)
    } finally {
      wrapper.unmount()
    }
  })

  it('健康接口失败时展示未连接和错误', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      request_id: 'req_audit', instance_id: 'inst_sidebar',
      reason_code: 'AUDIT_UNAVAILABLE', message: 'audit storage unavailable',
    }), { status: 503 })))
    const wrapper = mount(ServiceConnection)
    try {
      await flushPromises()
      expect(wrapper.get('.status-label').text()).toBe('未连接')
      expect(wrapper.text()).toContain('audit storage unavailable')
    } finally {
      wrapper.unmount()
    }
  })

  it('卸载时取消正在进行的健康请求', async () => {
    const signals: AbortSignal[] = []
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation((_url, init) => {
      if (init?.signal) signals.push(init.signal)
      return new Promise((_resolve, reject) =>
        init?.signal?.addEventListener('abort', () => reject(new DOMException('', 'AbortError'))),
      )
    }))
    const wrapper = mount(ServiceConnection)
    wrapper.unmount()
    expect(signals).toHaveLength(1)
    expect(signals[0]?.aborted).toBe(true)
    await flushPromises()
  })
})
