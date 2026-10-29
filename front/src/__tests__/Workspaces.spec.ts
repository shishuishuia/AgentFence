import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import WorkspacesView from '@/views/WorkspacesView.vue'
import * as api from '@/api/workspaces'

vi.mock('@/api/workspaces', () => ({
  openManagement: vi.fn<typeof api.openManagement>(),
  startPicker: vi.fn<typeof api.startPicker>(),
  getSelection: vi.fn<typeof api.getSelection>(),
  confirmWorkspace: vi.fn<typeof api.confirmWorkspace>(),
  getWorkspaces: vi.fn<typeof api.getWorkspaces>(),
  getExclusions: vi.fn<typeof api.getExclusions>(),
  getCredential: vi.fn<typeof api.getCredential>(),
  saveCredential: vi.fn<typeof api.saveCredential>(),
  clearCredential: vi.fn<typeof api.clearCredential>(),
}))
const id = `sel_${'a'.repeat(32)}`
const ready: api.Selection = {
  selection_id: id,
  state: 'ready',
  display_path: 'C:/fixture',
  preview: {
    files: [{ path: 'src/main.rs', bytes: 50, sha256: 'a'.repeat(64) }],
    allowed_file_count: 3,
    excluded_file_count: 1,
    total_bytes: 50,
    manifest_sha256: 'a'.repeat(64),
    expires_at: '2026-10-29T00:00:00Z',
    excluded: [{ path: '.env', reason_code: 'SENSITIVE_FILE' }],
  },
}
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.openManagement).mockResolvedValue(undefined)
  vi.mocked(api.getWorkspaces).mockResolvedValue({ items: [] })
  vi.mocked(api.startPicker).mockResolvedValue({ selection_id: id, state: 'pending' })
  vi.mocked(api.getSelection).mockResolvedValue(ready)
})
afterEach(() => vi.useRealTimers())

describe('外部 Agent 凭证', () => {
  it('只写提交：保存后清空输入且不回显密钥', async () => {
    vi.mocked(api.getCredential).mockResolvedValue({
      configured: false,
      source: null,
      editable: true,
    })
    vi.mocked(api.saveCredential).mockResolvedValue({
      configured: true,
      source: 'file',
      editable: true,
    })
    const wrapper = mount(WorkspacesView)
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('模型凭证设置'))!
      .trigger('click')
    await flushPromises()
    const key = 'sk-' + 'a'.repeat(32)
    await wrapper.get('input[type="password"]').setValue(key)
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('保存密钥'))!
      .trigger('click')
    await flushPromises()
    expect(api.saveCredential).toHaveBeenCalledWith(key)
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('本机已保存凭证')
    wrapper.unmount()
  })
})

describe('工作区导入', () => {
  it('预览后显式确认，并阻止重复确认', async () => {
    const wrapper = mount(WorkspacesView)
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(api.confirmWorkspace).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('允许 3 个文件')
    await wrapper.get('input').setValue('project')
    let finish!: (value: api.Workspace) => void
    vi.mocked(api.confirmWorkspace).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const confirm = wrapper.findAll('button')[1]!
    await confirm.trigger('click')
    await confirm.trigger('click')
    expect(api.confirmWorkspace).toHaveBeenCalledTimes(1)
    finish({} as api.Workspace)
    await flushPromises()
    expect(wrapper.text()).toContain('本次选择已使用')
    wrapper.unmount()
  })
  it('取消后不显示确认按钮', async () => {
    vi.mocked(api.getSelection).mockResolvedValue({ selection_id: id, state: 'cancelled' })
    const wrapper = mount(WorkspacesView)
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已取消')
    expect(wrapper.find('input').exists()).toBe(false)
    expect(api.confirmWorkspace).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('确认失败不会保留可重试的旧选择', async () => {
    vi.mocked(api.confirmWorkspace).mockRejectedValue(new Error('结果未知'))
    const wrapper = mount(WorkspacesView)
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    await wrapper.get('input').setValue('project')
    await wrapper.findAll('button')[1]!.trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('结果未知')
    expect(wrapper.find('input').exists()).toBe(false)
    wrapper.unmount()
  })
  it('卸载后停止目录轮询', async () => {
    vi.useFakeTimers()
    vi.mocked(api.getSelection).mockResolvedValue({ selection_id: id, state: 'pending' })
    const wrapper = mount(WorkspacesView)
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(1000)
    expect(api.getSelection).toHaveBeenCalledTimes(1)
  })
})
