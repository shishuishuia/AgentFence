<script setup lang="ts">
import { RouterLink } from 'vue-router'
import PageScaffold from '@/components/PageScaffold.vue'
defineProps<{ taskId: string }>()
</script>

<template>
  <PageScaffold
    title="任务详情"
    :description="`任务标识：${taskId}`"
    :endpoint="`GET /api/tasks/${encodeURIComponent(taskId)}`"
  >
    <RouterLink class="button" to="/tasks">返回任务列表</RouterLink>
  </PageScaffold>
</template>
