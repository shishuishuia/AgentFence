<script setup lang="ts">
import type { Phase } from '@/types/api'
defineProps<{ phase: Phase }>()
const labels: Record<Phase, string> = {
  inspect: 'Inspect · 检查',
  edit: 'Edit · 修改',
  verify: 'Verify · 验证',
  done: 'Done · 完成',
}
</script>
<template>
  <span class="phase-badge" :class="phase">{{ labels[phase] }}</span>
</template>
