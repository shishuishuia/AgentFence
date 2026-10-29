import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import ReadonlyTaskForm from '@/components/ReadonlyTaskForm.vue'
import { createReadonlyTask, createEditTask, getAgents, startTaskReview } from '@/api/workspaces'
import type { TaskSummary } from '@/types/api'
vi.mock('@/api/workspaces', () => ({ createReadonlyTask: vi.fn<typeof createReadonlyTask>(), createEditTask: vi.fn<typeof createEditTask>(), getAgents: vi.fn<typeof getAgents>(), startTaskReview: vi.fn<typeof startTaskReview>() }))
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(getAgents).mockResolvedValue({ builtin: true, deepseek_harness: false, builtin_mode: 'deepseek' })
  vi.mocked(startTaskReview).mockResolvedValue(undefined)
})
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
  await flushPromises()
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
    expect(startTaskReview).toHaveBeenCalledWith('task_readonly', 'builtin', expect.any(AbortSignal))
    expect(router.currentRoute.value.path).toBe('/tasks/task_readonly')
    wrapper.unmount()
  })
  it('选择读写任务后使用隔离副本接口', async () => {
    vi.mocked(createEditTask).mockResolvedValue({ task_id: 'task_edit' } as TaskSummary)
    const { wrapper, router } = await setup()
    await wrapper.get('input[value="controlled_edit"]').setValue()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(createEditTask).toHaveBeenCalledWith('ws_test', expect.any(String), expect.any(AbortSignal))
    expect(createReadonlyTask).not.toHaveBeenCalled()
    expect(startTaskReview).toHaveBeenCalledWith('task_edit', 'builtin', expect.any(AbortSignal))
    expect(router.currentRoute.value.path).toBe('/tasks/task_edit')
    wrapper.unmount()
  })
  it('未配置的外部 Agent 不可选', async () => {
    const { wrapper } = await setup()
    expect(wrapper.get('input[value="deepseek_harness"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('尚未配置完成')
    wrapper.unmount()
  })
  it('离线演示明确标识且禁止读写修改', async () => {
    vi.mocked(getAgents).mockResolvedValue({ builtin: true, deepseek_harness: false, builtin_mode: 'offline' })
    const { wrapper } = await setup()
    expect(wrapper.text()).toContain('离线演示（内置）')
    expect(wrapper.get('input[value="controlled_edit"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })
  it('无法查询 Agent 状态时不创建任务', async () => {
    vi.mocked(getAgents).mockRejectedValue(new Error('服务不可用'))
    const { wrapper } = await setup()
    await wrapper.get('form').trigger('submit')
    expect(createReadonlyTask).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('服务不可用')
    wrapper.unmount()
  })
  it('配置外部 Agent 时，按所选 Agent 启动并在读写模式禁用它', async () => {
    vi.mocked(getAgents).mockResolvedValue({ builtin: true, deepseek_harness: true, builtin_mode: 'deepseek' })
    vi.mocked(createReadonlyTask).mockResolvedValue({ task_id: 'task_external' } as TaskSummary)
    const { wrapper } = await setup()
    await wrapper.get('input[value="deepseek_harness"]').setValue()
    await wrapper.get('input[value="controlled_edit"]').setValue()
    expect(wrapper.get('input[value="deepseek_harness"]').attributes('disabled')).toBeDefined()
    await wrapper.get('input[value="readonly"]').setValue()
    await wrapper.get('input[value="deepseek_harness"]').setValue()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(startTaskReview).toHaveBeenCalledWith('task_external', 'deepseek_harness', expect.any(AbortSignal))
    wrapper.unmount()
  })
  it('启动结果不确定时保留任务链接，不重复创建或启动', async () => {
    vi.mocked(createReadonlyTask).mockResolvedValue({ task_id: 'task_created' } as TaskSummary)
    vi.mocked(startTaskReview).mockRejectedValue(new Error('timeout'))
    const { wrapper, router } = await setup()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    expect(createReadonlyTask).toHaveBeenCalledTimes(1)
    expect(startTaskReview).toHaveBeenCalledTimes(1)
    expect(wrapper.get('[role="alert"]').text()).toContain('任务已创建')
    expect(wrapper.get('a').attributes('href')).toBe('/tasks/task_created')
    expect(router.currentRoute.value.path).toBe('/')
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
