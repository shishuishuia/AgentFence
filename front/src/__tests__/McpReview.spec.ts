import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import McpReviewPanel from '@/components/McpReviewPanel.vue'
import { management, openManagement } from '@/api/workspaces'
vi.mock('@/api/workspaces', () => ({
  management: vi.fn<typeof management>(),
  openManagement: vi.fn<typeof openManagement>(),
}))
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(openManagement).mockResolvedValue(undefined)
})
describe('MCP 审查状态', () => {
  it('离线重放明确标注未调用模型', async () => {
    vi.mocked(management).mockResolvedValue({
      state: 'completed',
      mode: 'offline_mcp_replay',
      call_count: 5,
      read_file_count: 3,
      report: '',
    })
    const wrapper = mount(McpReviewPanel, { props: { taskId: 'task_test' } })
    await flushPromises()
    expect(wrapper.text()).toContain('未调用模型生成审查建议')
    expect(wrapper.find('pre').exists()).toBe(false)
    wrapper.unmount()
  })
  it('模型输出按纯文本显示', async () => {
    vi.mocked(management).mockResolvedValue({
      state: 'completed',
      mode: 'model_review',
      report: '<script>bad()</script>',
    })
    const wrapper = mount(McpReviewPanel, { props: { taskId: 'task_test' } })
    await flushPromises()
    expect(wrapper.get('pre').text()).toContain('<script>')
    expect(wrapper.find('script').exists()).toBe(false)
    wrapper.unmount()
  })
  it('重复点击只启动一次', async () => {
    vi.mocked(management)
      .mockResolvedValueOnce({ state: 'idle' })
      .mockImplementation(() => new Promise(() => {}))
    const wrapper = mount(McpReviewPanel, { props: { taskId: 'task_test' } })
    await flushPromises()
    const button = wrapper.get('button')
    await button.trigger('click')
    await button.trigger('click')
    expect(management).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })
})
