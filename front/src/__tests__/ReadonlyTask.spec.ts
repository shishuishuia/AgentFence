import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import ReadonlyTaskForm from '@/components/ReadonlyTaskForm.vue'
import { createReadonlyTask } from '@/api/workspaces'
import type { TaskSummary } from '@/types/api'
vi.mock('@/api/workspaces', () => ({ createReadonlyTask: vi.fn<typeof createReadonlyTask>() }))
beforeEach(() => vi.resetAllMocks())
async function setup() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/tasks/:taskId', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  const wrapper = mount(ReadonlyTaskForm, {
    props: { workspaceId: 'ws_test' },
    global: { plugins: [router] },
  })
  return { wrapper, router }
}
describe('只读任务创建', () => {
  it('创建成功后打开任务详情', async () => {
    vi.mocked(createReadonlyTask).mockResolvedValue({ task_id: 'task_readonly' } as TaskSummary)
    const { wrapper, router } = await setup()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(createReadonlyTask).toHaveBeenCalledWith(
      'ws_test',
      expect.any(String),
      expect.any(AbortSignal),
    )
    expect(router.currentRoute.value.path).toBe('/tasks/task_readonly')
    wrapper.unmount()
  })
  it('超时或重复提交只发送一次创建请求', async () => {
    vi.mocked(createReadonlyTask).mockRejectedValue(new Error('timeout'))
    const { wrapper } = await setup()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    expect(createReadonlyTask).toHaveBeenCalledTimes(1)
    expect(wrapper.get('[role="alert"]').text()).toContain('请先检查任务列表')
    wrapper.unmount()
  })
})
