<script setup lang="ts">
import { getTasks } from '@/api'
import { usePolling } from '@/composables/usePolling'
import RequestState from '@/components/RequestState.vue'
import TaskTable from '@/components/TaskTable.vue'
import LiveDemoPanel from '@/components/LiveDemoPanel.vue'
const list = usePolling((context) => getTasks(50, context.signal), { interval: 60000 })
</script>
<template>
  <section>
    <p class="eyebrow">TASKS</p>
    <h1>任务列表</h1>
    <p class="page-description">查询当前 Rust 实例中的最近任务，自动更新执行阶段。</p>
    <RequestState
      :loading="list.loading.value"
      :error="list.error.value"
      :updated-at="list.updatedAt.value"
      :has-data="!!list.data.value"
      @refresh="list.refresh"
    />
    <section class="panel">
      <div class="section-heading">
        <h2>最近任务</h2>
        <span v-if="list.data.value" class="scope-note"
          >当前实例共 {{ list.data.value.total }} 个 · 显示
          {{ list.data.value.items.length }} 个</span
        >
      </div>
      <p v-if="list.data.value?.truncated" class="scope-note">仅显示最近 50 个任务，列表已截断。</p>
      <TaskTable v-if="list.data.value" :tasks="list.data.value.items" />
      <p v-else class="empty-message">等待任务数据。</p>
    </section>
    <LiveDemoPanel hide-when-unavailable />
  </section>
</template>
