import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import LiveDemoPanel from '@/components/LiveDemoPanel.vue'
import type { DemoState } from '@/api/demo'

const idle: DemoState = {
  enabled: true,
  instance_id: 'inst_live_ui',
  run_id: null,
  status: 'idle',
  task_id: null,
  phase: null,
  completed_steps: 0,
  total_steps: 13,
  steps: [],
  failure: null,
  verified: false,
}
afterEach(() => vi.unstubAllGlobals())

describe('交互演示控制', () => {
  it('真实模型模式提交用户任务，不显示写死的 13 步进度', async () => {
    const modelState: DemoState = {
      ...idle,
      mode: 'llm',
      model: 'deepseek-flash',
      provider: 'DeepSeek',
      model_configured: true,
      total_steps: null,
      model_round: 0,
      max_rounds: 12,
    }
    const request = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => new Response(JSON.stringify(modelState)))
    vi.stubGlobal('fetch', request)
    const wrapper = mount(LiveDemoPanel, { global: { stubs: { RouterLink: RouterLinkStub } } })
    try {
      await flushPromises()
      await wrapper.get('textarea').setValue('把输出改为 Hello DeepSeek')
      await wrapper.get('button.primary').trigger('click')
      await flushPromises()
      const post = request.mock.calls.find((c) => c[1]?.method === 'POST')!
      expect(JSON.parse(String(post[1]!.body)).task).toBe('把输出改为 Hello DeepSeek')
      expect(wrapper.text()).toContain('DeepSeek / deepseek-flash')
      expect(wrapper.find('progress').exists()).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })

  it('服务端缺少模型凭证时禁用启动，不要求浏览器输入密钥', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(
          JSON.stringify({
            ...idle,
            mode: 'llm',
            total_steps: null,
            model_configured: false,
            model: 'deepseek-flash',
          }),
        ),
      ),
    )
    const wrapper = mount(LiveDemoPanel, { global: { stubs: { RouterLink: RouterLinkStub } } })
    try {
      await flushPromises()
      expect(wrapper.get('button.primary').attributes('disabled')).toBeDefined()
      expect(wrapper.text()).toContain('服务端未配置 DeepSeek 密钥')
      expect(wrapper.find('input[type=password]').exists()).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })
  it('非预期的 403 显示失败，不计为预期拦截', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(
          JSON.stringify({
            ...idle,
            run_id: 'run_expired',
            status: 'failed',
            completed_steps: 1,
            steps: [
              {
                case: 'edit_write',
                http_status: 403,
                reason_code: 'LEASE_EXPIRED',
                request_id: 'req_expired',
                allowed: false,
                executed: false,
                pass: false,
              },
            ],
            failure: '租约已过期',
          }),
        ),
      ),
    )
    const wrapper = mount(LiveDemoPanel, { global: { stubs: { RouterLink: RouterLinkStub } } })
    try {
      await flushPromises()
      expect(wrapper.text()).toContain('非预期响应')
      expect(wrapper.text()).toContain('LEASE_EXPIRED')
      expect(wrapper.text()).not.toContain('预期拦截 · 403')
    } finally {
      wrapper.unmount()
    }
  })
  it('按钮启动固定任务，重复点击不重复提交，随后显示可暂停的进度', async () => {
    let state: DemoState = { ...idle }
    let release: (() => void) | undefined
    const request = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
      if (init?.method === 'POST') {
        await new Promise<void>((resolve) => {
          release = resolve
        })
        state = { ...state, status: 'running', run_id: 'run_ui' }
      }
      return new Response(JSON.stringify(state))
    })
    vi.stubGlobal('fetch', request)
    const wrapper = mount(LiveDemoPanel, { global: { stubs: { RouterLink: RouterLinkStub } } })
    try {
      await flushPromises()
      const start = wrapper.get('button.primary')
      await start.trigger('click')
      await start.trigger('click')
      expect(request.mock.calls.filter((c) => c[1]?.method === 'POST')).toHaveLength(1)
      expect(start.attributes('disabled')).toBeDefined()
      const init = request.mock.calls.find((c) => c[1]?.method === 'POST')![1]!
      expect(JSON.parse(String(init.body))).toEqual({
        action: 'start',
        request_id: expect.any(String),
      })
      expect(init.credentials).toBe('omit')
      expect(init.headers).toEqual({ 'Content-Type': 'application/json', 'X-AgentFence-Demo': '1' })
      release!()
      await flushPromises()
      expect(wrapper.text()).toContain('暂停演示')
      expect(wrapper.text()).toContain('0 / 13 步')
    } finally {
      wrapper.unmount()
    }
  })

  it('只读服务明确说明如何启用，禁用启动按钮', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(
          JSON.stringify({
            reason_code: 'ROUTE_NOT_FOUND',
            request_id: 'req_disabled',
            instance_id: null,
            message: 'disabled',
          }),
          { status: 404 },
        ),
      ),
    )
    const wrapper = mount(LiveDemoPanel, { global: { stubs: { RouterLink: RouterLinkStub } } })
    try {
      await flushPromises()
      expect(wrapper.text()).toContain('当前服务为只读展示')
      expect(wrapper.get('button.primary').attributes('disabled')).toBeDefined()
    } finally {
      wrapper.unmount()
    }
  })

  it('未知执行结果不会自动重试 POST，用户再次尝试沿用幂等键', async () => {
    const request = vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
      if (init?.method === 'POST') throw new TypeError('connection lost')
      return new Response(JSON.stringify(idle))
    })
    vi.stubGlobal('fetch', request)
    const wrapper = mount(LiveDemoPanel, { global: { stubs: { RouterLink: RouterLinkStub } } })
    try {
      await flushPromises()
      await wrapper.get('button.primary').trigger('click')
      await flushPromises()
      expect(request.mock.calls.filter((c) => c[1]?.method === 'POST')).toHaveLength(1)
      await wrapper.get('button.primary').trigger('click')
      await flushPromises()
      const posts = request.mock.calls.filter((c) => c[1]?.method === 'POST')
      expect(posts).toHaveLength(2)
      expect(posts[0]![1]!.body).toBe(posts[1]![1]!.body)
    } finally {
      wrapper.unmount()
    }
  })
})
