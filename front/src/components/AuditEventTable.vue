<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AuditEvent } from '@/types/api'
import { classify, formatTime, pendingRequests, type ResultFilter } from '@/utils/audit'
const props = withDefaults(defineProps<{ events: AuditEvent[]; filter?: ResultFilter }>(), {
  filter: 'all',
})
const selectedRequest = ref<string | null>(null)
watch(
  () => props.filter,
  () => {
    selectedRequest.value = null
  },
)
watch(
  () => props.events,
  () => {
    if (selectedRequest.value && !props.events.some((e) => e.request_id === selectedRequest.value))
      selectedRequest.value = null
  },
)
const pending = computed(() => pendingRequests(props.events))
const rows = computed(() =>
  props.events
    .filter((event) =>
      selectedRequest.value
        ? event.request_id === selectedRequest.value
        : props.filter === 'all' || classify(event) === props.filter,
    )
    .reverse(),
)
const labels = { allowed: '成功', denied: '策略拒绝', failed: '失败' }
function label(event: AuditEvent) {
  const category = classify(event)
  if (category) return labels[category]
  if (event.event_type !== 'intent') return '管理事件'
  return pending.value.has(`${event.instance_id}:${event.request_id}`)
    ? '执行结果未确认'
    : '调用意图'
}
</script>
<template>
  <p v-if="selectedRequest" class="scope-note">
    正在关联请求 {{ selectedRequest }}
    <button class="button compact" @click="selectedRequest = null">返回全部</button>
  </p>
  <p v-if="!rows.length" class="empty-message">当前加载范围内没有匹配的审计事件。</p>
  <div v-else class="table-scroll">
    <table class="audit-table">
      <thead>
        <tr>
          <th>事件 / 时间</th>
          <th>任务 / 阶段</th>
          <th>工具 / 路径</th>
          <th>结果</th>
          <th>请求关联</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="event in rows" :key="`${event.instance_id}:${event.event_id}`">
          <td>
            <strong>#{{ event.event_id }} · {{ event.event_type }}</strong
            ><small>{{ formatTime(event.timestamp) }}</small>
          </td>
          <td>
            <span class="mono">{{ event.task_id || '—' }}</span
            ><small>{{ event.phase || '—' }}</small>
          </td>
          <td>
            {{ event.tool || '管理操作' }}<small class="mono">{{ event.path || '—' }}</small>
          </td>
          <td>
            <span class="result-badge" :class="classify(event) || 'neutral'">{{
              label(event)
            }}</span
            ><small class="mono">{{ event.reason_code }}</small
            ><small v-if="event.event_type === 'result'"
              >允许 {{ event.allowed ? '是' : '否' }} · 已执行
              {{ event.executed ? '是' : '否' }}</small
            >
          </td>
          <td>
            <button class="request-link mono" @click="selectedRequest = event.request_id">
              {{ event.request_id }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
