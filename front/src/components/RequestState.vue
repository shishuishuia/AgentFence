<script setup lang="ts">
import { ApiError } from '@/api/client'
import { formatTime } from '@/utils/audit'
defineProps<{ loading: boolean; error: Error | null; updatedAt: string | null; hasData: boolean }>()
defineEmits<{ refresh: [] }>()
</script>
<template>
  <div class="request-state" :class="{ 'has-error': error }" role="status" aria-live="polite">
    <span v-if="error"
      >{{ hasData ? '刷新失败，保留上次数据：' : '加载失败：' }}{{ error.message
      }}<small v-if="error instanceof ApiError">
        {{ error.reasonCode
        }}<template v-if="error.requestId"> · {{ error.requestId }}</template></small
      ></span
    >
    <span v-else-if="loading">{{ hasData ? '正在刷新…' : '正在加载…' }}</span
    ><span v-else>自动更新中</span>
    <span v-if="updatedAt"
      >上次成功：{{ formatTime(updatedAt) }}<template v-if="error">（快照可能过时）</template></span
    >
    <button class="button compact" @click="$emit('refresh')">重新加载</button>
  </div>
</template>
