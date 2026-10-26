import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from '../App.vue'
import { routes } from '../router'

describe('页面路由', () => {
  it('访问根目录会重定向到总览', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [router] } })
    expect(router.currentRoute.value.path).toBe('/dashboard')
    expect(wrapper.get('h1').text()).toBe('项目总览')
    wrapper.unmount()
  })

  it('支持直接进入任务详情并返回列表', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/tasks/task-123')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [router] } })
    expect(wrapper.text()).toContain('任务标识：task-123')
    await router.push('/tasks')
    expect(wrapper.get('h1').text()).toBe('任务列表')
    wrapper.unmount()
  })

  it('未知页面进入404路由', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/unknown-page')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [router] } })
    expect(wrapper.get('h1').text()).toBe('页面不存在')
    wrapper.unmount()
  })
})
