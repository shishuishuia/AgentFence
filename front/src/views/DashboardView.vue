<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { getHealth } from '@/api'
import { ApiError } from '@/api/client'

const state = ref<'idle' | 'loading' | 'connected' | 'failed'>('idle')
const message = ref('点击检测，确认本地只读代理和 Rust 服务是否可用。')
let controller: AbortController | undefined

async function checkConnection() {
  controller?.abort()
  const current = new AbortController()
  controller = current
  state.value = 'loading'
  const timeout = setTimeout(() => current.abort(), 6000)
  try {
    const health = await getHealth(current.signal)
    if (current.signal.aborted) return
    state.value = health.status === 'ok' ? 'connected' : 'failed'
    message.value = health.status === 'ok' ? 'Rust 服务正常，审计可用。' : 'Rust 服务状态异常。'
  } catch (error) {
    if (controller !== current) return
    state.value = 'failed'
    message.value =
      error instanceof ApiError ? error.message : '暂时无法连接，请检查本地 4173 端口的只读代理。'
  } finally {
    clearTimeout(timeout)
  }
}

onBeforeUnmount(() => {
  controller?.abort()
  controller = undefined
})
</script>

<template>
  <section>
    <p class="eyebrow">AGENTFENCE WORKSPACE</p>
    <h1>项目总览</h1>
    <p class="page-description">围绕任务阶段，观察工具权限与审计记录。</p>
    <div class="hero panel">
      <div>
        <span class="pill">前端工程已创建</span>
        <h2>从可见的权限开始</h2>
        <p>Vue 页面、路由和只读 API 入口已准备好。下一步连接 Rust 服务，展示任务执行过程。</p>
        <RouterLink class="button primary" to="/tasks">查看任务页面 <span>→</span></RouterLink>
      </div>
      <div class="phase-flow" aria-label="任务阶段">
        <span>01 <strong>Inspect</strong><small>读取代码</small></span>
        <span>02 <strong>Edit</strong><small>受控修改</small></span>
        <span>03 <strong>Verify</strong><small>验证结果</small></span>
        <span>04 <strong>Done</strong><small>结束任务</small></span>
      </div>
    </div>
    <div class="overview-grid">
      <section class="panel connection-panel">
        <div class="section-heading">
          <h2>服务连接</h2>
          <span class="status-label" :class="state">{{
            state === 'connected'
              ? '已连接'
              : state === 'loading'
                ? '检测中'
                : state === 'failed'
                  ? '未连接'
                  : '未检测'
          }}</span>
        </div>
        <p role="status" aria-live="polite">{{ message }}</p>
        <button class="button" :disabled="state === 'loading'" @click="checkConnection">
          {{ state === 'loading' ? '正在检测…' : '检测连接' }}
        </button>
      </section>
      <section class="panel">
        <h2>开始联调</h2>
        <ul class="setup-list">
          <li><span>01</span>启动 Rust Broker 与本地只读代理</li>
          <li><span>02</span>通过管理员 CLI 创建任务和发放租约</li>
          <li><span>03</span>接入任务列表与审计事件</li>
        </ul>
      </section>
    </div>
  </section>
</template>
