<script setup lang="ts">
import PageScaffold from '@/components/PageScaffold.vue'
</script>

<template>
  <PageScaffold
    title="审计事件"
    description="跟踪工具调用、权限决策和任务状态变化。"
    endpoint="GET /api/events"
  />
</template>
