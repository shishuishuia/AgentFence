<script setup lang="ts">
defineProps<{ title: string; description: string; endpoint: string }>()
</script>

<template>
  <section>
    <p class="eyebrow">AGENTFENCE WORKSPACE</p>
    <h1>{{ title }}</h1>
    <p class="page-description">{{ description }}</p>
    <div class="panel empty-state">
      <span class="empty-symbol" aria-hidden="true">◈</span>
      <h2>页面骨架已就绪</h2>
      <p>后续在此接入真实业务数据。当前未展示模拟任务或审计结果。</p>
      <code>{{ endpoint }}</code>
      <slot />
    </div>
  </section>
</template>
