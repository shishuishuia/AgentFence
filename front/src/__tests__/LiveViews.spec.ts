import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
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
      // The workspace ID is shortened for display; the full value stays only in
      // a title attribute and must never become an element.
      expect(wrapper.text()).toContain('<img src…')
      expect(wrapper.find('img').exists()).toBe(false)
      const titled = wrapper.findAll('td[title]').map((cell) => cell.attributes('title'))
      expect(titled).toContain('<img src=x onerror=alert(1)>')
    } finally {
      wrapper.unmount()
    }
  })
})
