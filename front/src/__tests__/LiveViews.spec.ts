import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import AuditView from '@/views/AuditView.vue'
import TaskDetailView from '@/views/TaskDetailView.vue'
import TaskListView from '@/views/TaskListView.vue'

afterEach(() => vi.unstubAllGlobals())

describe('真实数据页面', () => {
  it('任务 404 展示业务错误并保留返回入口', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockImplementation(
        async (url) =>
          new Response(
            JSON.stringify(
              String(url).includes('/events')
                ? { instance_id: 'inst_view', events: [], next_cursor: 'opaque', has_more: false }
                : {
                    instance_id: 'inst_view',
                    request_id: 'req_missing',
                    reason_code: 'TASK_NOT_FOUND',
                    message: 'task missing',
                  },
            ),
            { status: String(url).includes('/events') ? 200 : 404 },
          ),
      ),
    )
    const wrapper = mount(TaskDetailView, {
      props: { taskId: 'task_missing' },
      global: { stubs: { RouterLink: RouterLinkStub } },
    })
    try {
      await flushPromises()
      expect(wrapper.get('h1').text()).toBe('任务详情')
      expect(wrapper.text()).toContain('当前实例不存在该任务')
      expect(wrapper.text()).toContain('返回任务列表')
    } finally {
      wrapper.unmount()
    }
  })

  it('结果筛选保留在路由，GET 不发送 result，修改结果不重拉事件', async () => {
    const request = vi.fn<typeof fetch>().mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            instance_id: 'inst_view',
            events: [],
            next_cursor: 'opaque',
            has_more: false,
          }),
        ),
    )
    vi.stubGlobal('fetch', request)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/audit', component: AuditView }],
    })
    await router.push('/audit?taskId=task_1&result=denied')
    const wrapper = mount(AuditView, { global: { plugins: [router] } })
    try {
      await flushPromises()
      expect(wrapper.get('input').element.value).toBe('task_1')
      expect(wrapper.get('select').element.value).toBe('denied')
      expect(String(request.mock.calls[0]![0])).toContain('task_id=task_1')
      expect(String(request.mock.calls[0]![0])).not.toContain('result')
      const before = request.mock.calls.length
      await router.push('/audit?taskId=task_1&result=failed')
      await flushPromises()
      expect(request).toHaveBeenCalledTimes(before)
      expect(wrapper.get('select').element.value).toBe('failed')
    } finally {
      wrapper.unmount()
    }
  })

  it('任务列表明确显示截断范围，外部文本不会解析成 HTML', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>().mockImplementation(
        async () =>
          new Response(
            JSON.stringify({
              instance_id: 'inst_view',
              total: 51,
              truncated: true,
              items: [
                {
                  task_id: 'task_1',
                  workspace_id: '<img src=x onerror=alert(1)>',
                  phase: 'inspect',
                  created_at: '2026-10-27T00:00:00Z',
                  updated_at: '2026-10-27T00:00:00Z',
                },
              ],
            }),
          ),
      ),
    )
    const wrapper = mount(TaskListView, { global: { stubs: { RouterLink: RouterLinkStub } } })
    try {
      await flushPromises()
      expect(wrapper.text()).toContain('当前实例共 51 个')
      expect(wrapper.text()).toContain('列表已截断')
      expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>')
      expect(wrapper.find('img').exists()).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })
})
