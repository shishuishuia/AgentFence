<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { openManagement, management } from '@/api/workspaces'
const props = defineProps<{ taskId: string }>()
interface Review {
  state: 'idle' | 'running' | 'completed' | 'failed'
  call_count?: number
  read_file_count?: number
  mode?: string
  report?: string
  message?: string
  calls?: { tool: string; request_id?: string; ok: boolean; path?: string }[]
}
const value = ref<Review | null>(null)
const error = ref('')
const busy = ref(false)
const controller = new AbortController()
let timer: ReturnType<typeof setTimeout> | undefined
const endpoint = () => `/tasks/${encodeURIComponent(props.taskId)}/review`
async function load() {
  try {
    const result = await management<Review>(endpoint(), undefined, controller.signal)
    if (!result || !['idle', 'running', 'completed', 'failed'].includes(result.state))
      throw new Error('审查状态响应无效。')
    if (controller.signal.aborted) return
    value.value = result
    if (result.state === 'running') timer = setTimeout(() => void load(), 1000)
  } catch (e) {
    if (!controller.signal.aborted) error.value = e instanceof Error ? e.message : '查询失败。'
  }
}
onMounted(async () => {
  try {
    await openManagement(controller.signal)
    await load()
  } catch (e) {
    if (!controller.signal.aborted) error.value = e instanceof Error ? e.message : '管理会话失败。'
  }
})
onUnmounted(() => {
  controller.abort()
  clearTimeout(timer)
})
async function start() {
  if (busy.value || value.value?.state !== 'idle') return
  busy.value = true
  error.value = ''
  value.value = { state: 'running' }
  try {
    await management(endpoint() + '/start', {}, controller.signal)
    await load()
  } catch (e) {
    if (!controller.signal.aborted) {
      error.value = e instanceof Error ? e.message : '启动结果未知，请刷新查询。'
      await load()
    }
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <section class="panel section-gap">
    <h2>MCP 只读审查</h2>
    <p v-if="error" class="notice" role="alert">{{ error }}</p>
    <template v-if="value">
      <button v-if="value.state === 'idle'" :disabled="busy" @click="start">开始 MCP 审查</button>
      <p role="status">
        状态：{{ value.state }} · 工具调用 {{ value.call_count || 0 }} 次 · 已读
        {{ value.read_file_count || 0 }} 个文件
      </p>
      <p v-if="value.mode === 'offline_mcp_replay'" class="notice">
        离线 MCP 调用重放：已核验文件与审计，未调用模型生成审查建议。
      </p>
      <p v-if="value.mode === 'model_review'">真实模型审查；建议需人工复核。</p>
      <p v-if="value.message" role="alert">{{ value.message }}</p>
      <pre v-if="value.report" class="review-report">{{ value.report }}</pre>
      <ul v-if="value.calls">
        <li v-for="(call, index) in value.calls" :key="index">
          <code>{{ call.tool }} {{ call.path || '' }}</code> ·
          {{ call.ok ? '成功' : '拒绝/失败' }} ·
          <code>{{ call.request_id || '未进入 Broker' }}</code>
        </li>
      </ul>
    </template>
  </section>
</template>
<style scoped>
.review-report {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
}
</style>
