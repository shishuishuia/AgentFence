import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DashboardView from '../views/DashboardView.vue'

const instance = 'inst_real'
function payload(url: string) {
  if (url.includes('/health')) return { status: 'ok', instance_id: instance }
  if (url.includes('/tasks'))
    return { items: [], total: 0, truncated: false, instance_id: instance }
  return { events: [], next_cursor: 'opaque', has_more: false, instance_id: instance }
}
afterEach(() => vi.unstubAllGlobals())

describe('真实健康接口契约', () => {
  it('最简健康响应可显示连接，页面进入时自动加载', async () => {
    const fetchHealth = vi
      .fn<typeof fetch>()
      .mockImplementation(async (url) => new Response(JSON.stringify(payload(String(url)))))
    vi.stubGlobal('fetch', fetchHealth)
    const wrapper = mount(DashboardView, { global: { stubs: { RouterLink: true } } })
    try {
      await flushPromises()
      expect(fetchHealth).toHaveBeenCalledWith(
        '/api/health',
        expect.objectContaining({ method: 'GET' }),
      )
      expect(wrapper.find('.status-label').text()).toBe('已连接')
      expect(wrapper.text()).toContain(instance)
      expect(wrapper.text()).toContain('当前实例暂无任务')
    } finally {
      wrapper.unmount()
    }
  })

  it('健康 503 保留说明，任务与审计仍独立查询', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockImplementation(async (url) => {
        if (String(url).includes('/health'))
          return new Response(
            JSON.stringify({
              request_id: 'req_audit',
              instance_id: instance,
              reason_code: 'AUDIT_UNAVAILABLE',
              message: 'audit storage unavailable',
            }),
            { status: 503 },
          )
        return new Response(JSON.stringify(payload(String(url))))
      }),
    )
    const wrapper = mount(DashboardView, { global: { stubs: { RouterLink: true } } })
    try {
      await flushPromises()
      expect(wrapper.find('.status-label').text()).toBe('未连接')
      expect(wrapper.text()).toContain('audit storage unavailable')
      expect(wrapper.text()).toContain('当前实例暂无任务')
      expect(wrapper.text()).toContain('当前加载的 0 条事件')
    } finally {
      wrapper.unmount()
    }
  })

  it('离开页面时取消所有进行中的资源请求', async () => {
    const signals: AbortSignal[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockImplementation((_url, init) => {
        const signal = init?.signal
        if (signal) signals.push(signal)
        return new Promise((_resolve, reject) =>
          signal?.addEventListener('abort', () => reject(new DOMException('', 'AbortError'))),
        )
      }),
    )
    const wrapper = mount(DashboardView, { global: { stubs: { RouterLink: true } } })
    wrapper.unmount()
    expect(signals.length).toBe(3)
    expect(signals.every((signal) => signal.aborted)).toBe(true)
    await flushPromises()
  })
})
