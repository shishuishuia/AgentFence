<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AuditEvent } from '@/types/api'
import {
  classify,
  eventLabel,
  formatTime,
  pendingRequests,
  phaseLabel,
  reasonLabel,
  shortId,
  shortRequest,
  toolLabel,
  type ResultFilter,
} from '@/utils/audit'
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
const labels = { allowed: '执行成功', denied: '策略拒绝', failed: '执行失败' }
function label(event: AuditEvent) {
  const category = classify(event)
  if (category) return labels[category]
  if (event.event_type !== 'intent') return eventLabel(event.event_type)
  return pending.value.has(`${event.instance_id}:${event.request_id}`)
    ? '结果未确认'
    : '调用前记录'
}
</script>
<template>
  <details class="audit-legend">
    <summary>字段说明</summary>
    <ul>
      <li><b>事件</b>：调用前与执行结果成对出现；管理事件包括阶段变更、租约签发/撤销、工作区导入。</li>
      <li><b>结果</b>：执行成功＝策略允许且已执行；策略拒绝＝阶段、路径或租约不允许；执行失败＝输入或文件操作出错。</li>
      <li><b>请求关联</b>：同一请求的调用前与结果共享请求 ID，点击可按请求筛选。</li>
    </ul>
  </details>
  <p v-if="selectedRequest" class="scope-note">
    正在关联请求 <span class="mono" :title="selectedRequest">{{ shortRequest(selectedRequest) }}</span>
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
            <strong>#{{ event.event_id }} · {{ eventLabel(event.event_type) }}</strong
            ><small>{{ formatTime(event.timestamp) }}</small>
          </td>
          <td>
            <span class="mono" :title="event.task_id || ''">{{ shortId(event.task_id) }}</span
            ><small>{{ phaseLabel(event.phase) }}</small>
          </td>
          <td>
            {{ toolLabel(event.tool) }}<small v-if="event.path" class="mono">{{ event.path }}</small>
          </td>
          <td>
            <span class="result-badge" :class="classify(event) || 'neutral'">{{
              label(event)
            }}</span
            ><small :title="event.reason_code">{{ reasonLabel(event.reason_code) }}</small
            ><small v-if="event.event_type === 'result'"
              >{{ event.allowed ? '允许执行' : '未允许' }} ·
              {{ event.executed ? '已执行' : '未执行' }}</small
            >
          </td>
          <td>
            <button
              class="request-link mono"
              :title="event.request_id"
              @click="selectedRequest = event.request_id"
            >
              {{ shortRequest(event.request_id) }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
<style scoped>
.audit-legend {
  margin: 6px 0 14px;
  font-size: 12px;
  color: #536b5e;
}
.audit-legend summary {
  cursor: pointer;
  font-weight: 600;
}
.audit-legend ul {
  margin: 8px 0 0;
  padding-left: 18px;
  line-height: 1.8;
}
</style>
