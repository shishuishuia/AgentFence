import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import DashboardView from '../views/DashboardView.vue'

afterEach(() => vi.unstubAllGlobals())

describe('后端健康契约', () => {
  it('仅返回 status 和 instance_id 时仍显示已连接', async () => {
    const fetchHealth = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok', instance_id: 'inst-test' }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchHealth)
    const wrapper = mount(DashboardView, { global: { stubs: { RouterLink: true } } })
    try {
      await wrapper.get('button').trigger('click')
      await flushPromises()
      expect(fetchHealth).toHaveBeenCalledWith('/api/health', expect.objectContaining({ method: 'GET' }))
      expect(wrapper.get('.status-label').text()).toBe('已连接')
      expect(wrapper.get('[role="status"]').text()).toBe('Rust 服务正常，审计可用。')
    } finally {
      wrapper.unmount()
    }
  })

  it('审计故障的 503 显示后端错误', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            request_id: 'req-test',
            instance_id: 'inst-test',
            reason_code: 'AUDIT_UNAVAILABLE',
            message: '审计不可用',
          }),
          { status: 503 },
        ),
      ),
    )
    const wrapper = mount(DashboardView, { global: { stubs: { RouterLink: true } } })
    try {
      await wrapper.get('button').trigger('click')
      await flushPromises()
      expect(wrapper.get('.status-label').text()).toBe('未连接')
      expect(wrapper.get('[role="status"]').text()).toBe('审计不可用')
    } finally {
      wrapper.unmount()
    }
  })
})
