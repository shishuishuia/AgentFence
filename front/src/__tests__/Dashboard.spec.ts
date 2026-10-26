import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DashboardView from '../views/DashboardView.vue'

afterEach(() => vi.unstubAllGlobals())

describe('真实健康接口契约', () => {
  it('仅有 status 和 instance_id 的正常响应也能显示已连接', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: 'ok', instance_id: 'inst_real' })),
      ),
    )
    const wrapper = mount(DashboardView, { global: { stubs: { RouterLink: true } } })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.status-label').text()).toBe('已连接')
    expect(wrapper.get('[role="status"]').text()).toContain('inst_real')
    wrapper.unmount()
  })

  it('审计失效的 503 保留后端错误说明，不显示已连接', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            request_id: 'req_audit',
            instance_id: 'inst_real',
            reason_code: 'AUDIT_UNAVAILABLE',
            message: 'audit storage unavailable',
          }),
          { status: 503 },
        ),
      ),
    )
    const wrapper = mount(DashboardView, { global: { stubs: { RouterLink: true } } })
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.status-label').text()).toBe('未连接')
    expect(wrapper.get('[role="status"]').text()).toContain('audit storage unavailable')
    wrapper.unmount()
  })

  it('离开页面时取消进行中的健康请求', async () => {
    let requestSignal: AbortSignal | undefined
    vi.stubGlobal(
      'fetch',
      vi.fn((_url, init: RequestInit) => {
        requestSignal = init.signal ?? undefined
        return new Promise((_resolve, reject) => {
          requestSignal?.addEventListener('abort', () => reject(new DOMException('', 'AbortError')))
        })
      }),
    )
    const wrapper = mount(DashboardView, { global: { stubs: { RouterLink: true } } })
    await wrapper.get('button').trigger('click')
    wrapper.unmount()
    expect(requestSignal?.aborted).toBe(true)
    await flushPromises()
  })
})
