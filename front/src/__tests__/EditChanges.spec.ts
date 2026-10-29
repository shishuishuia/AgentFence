import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import EditChangesPanel from '@/components/EditChangesPanel.vue'
import { management, openManagement } from '@/api/workspaces'
vi.mock('@/api/workspaces', () => ({ management: vi.fn<typeof management>(), openManagement: vi.fn<typeof openManagement>() }))
beforeEach(() => { vi.resetAllMocks(); vi.mocked(openManagement).mockResolvedValue(undefined) })
describe('确认隔离修改', () => {
  it('只有预览文件并勾选确认后才能提交', async () => {
    const change = {path: 'main.rs', before_sha256: 'a'.repeat(64), after_sha256: 'b'.repeat(64)}
    vi.mocked(management).mockResolvedValueOnce({changes: [change]}).mockResolvedValueOnce({...change, before: '<script>old</script>', after: 'new'}).mockResolvedValueOnce({})
    const wrapper = mount(EditChangesPanel, {props: {taskId: 'task_test'}})
    await flushPromises()
    await wrapper.get('li button').trigger('click')
    await flushPromises()
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.text()).toContain('<script>old</script>')
    const button = wrapper.findAll('button').find((b) => b.text().includes('确认应用'))!
    expect(button.attributes('disabled')).toBeDefined()
    await wrapper.get('input[type=checkbox]').setValue(true)
    await button.trigger('click')
    await flushPromises()
    expect(management).toHaveBeenCalledWith('/tasks/task_test/apply', {path: 'main.rs', after_sha256: 'b'.repeat(64)})
    expect(wrapper.text()).toContain('已应用')
    wrapper.unmount()
  })
})
