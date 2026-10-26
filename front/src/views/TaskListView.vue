<script setup lang="ts">
import PageScaffold from '@/components/PageScaffold.vue'
</script>

<template>
  <PageScaffold
    title="任务列表"
    description="查看当前服务实例中的任务与阶段。"
    endpoint="GET /api/tasks"
  />
</template>
