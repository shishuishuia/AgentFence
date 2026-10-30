<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import './assets/main.css'
import { useBrokerSession } from '@/composables/useBrokerSession'
import ServiceConnection from '@/components/ServiceConnection.vue'

const route = useRoute()
const isLanding = computed(() => route.meta.layout === 'landing')
const session = useBrokerSession()
const logoUrl = `${import.meta.env.BASE_URL}favicon.svg`
</script>

<template>
  <RouterView v-if="isLanding" />
  <div v-else class="app-shell">
    <aside class="sidebar">
      <RouterLink class="brand" to="/" aria-label="AgentFence 首页">
        <img :src="logoUrl" alt="" width="36" height="36" />
        <span>AgentFence<small>LITE CONSOLE</small></span>
      </RouterLink>
      <p class="nav-label">工作空间</p>
      <nav aria-label="主导航">
        <RouterLink to="/workspaces"><span class="nav-icon">▧</span>工作区管理</RouterLink>
        <RouterLink to="/tasks"><span class="nav-icon">▤</span>任务列表</RouterLink>
      </nav>
      <RouterLink class="nav-back" to="/">← 返回欢迎页</RouterLink>
      <ServiceConnection />
    </aside>
    <div class="workspace">
      <header class="topbar">
        <div class="breadcrumb">工作空间 <span>/</span> {{ route.meta.title }}</div>
        <span class="environment">LOCAL</span>
      </header>
      <main id="main-content">
        <p v-if="session.restarted.value" class="notice" role="status">
          服务实例已变化，已清空旧实例数据并重新查询。
        </p>
        <RouterView />
      </main>
      <footer>AgentFence Lite <span>基于 Rust 的 AI Agent 文件操作权限网关</span></footer>
    </div>
  </div>
</template>
