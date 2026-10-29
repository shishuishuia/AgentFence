import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from '../App.vue'
import { routes } from '../router'

describe('页面路由', () => {
  it('访问根目录和旧总览地址都进入工作区，连接状态在主导航下方', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [router] } })
    expect(router.currentRoute.value.path).toBe('/workspaces')
    expect(wrapper.get('h1').text()).toBe('工作区管理')
    expect(wrapper.find('nav').text()).not.toContain('项目总览')
    expect(wrapper.get('.sidebar .service-connection').text()).toContain('服务连接')
    await router.push('/dashboard')
    expect(router.currentRoute.value.path).toBe('/workspaces')
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
