<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'
import './assets/main.css'
import { useBrokerSession } from '@/composables/useBrokerSession'

const route = useRoute()
const session = useBrokerSession()
const logoUrl = `${import.meta.env.BASE_URL}favicon.svg`
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <RouterLink class="brand" to="/dashboard" aria-label="AgentFence 首页">
        <img :src="logoUrl" alt="" width="36" height="36" />
        <span>AgentFence<small>LITE CONSOLE</small></span>
      </RouterLink>
      <p class="nav-label">工作空间</p>
      <nav aria-label="主导航">
        <RouterLink to="/dashboard"><span class="nav-icon">◈</span>项目总览</RouterLink>
        <RouterLink to="/tasks"><span class="nav-icon">▤</span>任务列表</RouterLink>
        <RouterLink to="/audit"><span class="nav-icon">◎</span>审计事件</RouterLink>
      </nav>
      <div class="sidebar-note">
        <span class="status-dot"></span> 本地任务控制台
        <p>查询真实任务，现场演示权限控制。凭证仅保留在服务端。</p>
      </div>
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
      <footer>AgentFence Lite <span>任务感知的工具权限控制</span></footer>
    </div>
  </div>
</template>
