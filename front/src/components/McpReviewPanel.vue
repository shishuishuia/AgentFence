<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { openManagement, management } from '@/api/workspaces'
import { ApiError } from '@/api/client'
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
const startError = ref('')
const busy = ref(false)
const querying = ref(false)
const retryHint = ref('')
const retryDelays = [1000, 2000, 4000]
let failures = 0
let sessionReady = false
let disposed = false
let activeRequest: AbortController | undefined
let timer: ReturnType<typeof setTimeout> | undefined
const endpoint = () => `/tasks/${encodeURIComponent(props.taskId)}/review`

// Bound both the connection and response body, without retrying a mutation.
async function request<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const controller = new AbortController()
  activeRequest = controller
  let rejectAbort!: () => void
  const aborted = new Promise<never>((_, reject) => {
    rejectAbort = () => reject(controller.signal.reason)
    controller.signal.addEventListener('abort', rejectAbort, { once: true })
  })
  const timeout = setTimeout(() => controller.abort(new Error('请求超时，请重新查询状态。')), 10000)
  try {
    return await Promise.race([operation(controller.signal), aborted])
  } finally {
    clearTimeout(timeout)
    controller.signal.removeEventListener('abort', rejectAbort)
    if (activeRequest === controller) activeRequest = undefined
  }
}

function schedule(delay: number) {
  clearTimeout(timer)
  timer = setTimeout(() => void load(), delay)
}

async function load() {
  if (disposed || querying.value || busy.value) return
  clearTimeout(timer)
  querying.value = true
  retryHint.value = ''
  try {
    if (!sessionReady) {
      await request(openManagement)
      if (disposed) return
      sessionReady = true
    }
    const result = await request((signal) => management<Review>(endpoint(), undefined, signal))
    if (!result || !['idle', 'running', 'completed', 'failed'].includes(result.state))
      throw new Error('审查状态响应无效。')
    if (disposed) return
    value.value = result
    error.value = ''
    if (result.state !== 'idle') startError.value = ''
    failures = 0
    if (result.state === 'running') schedule(1000)
  } catch (e) {
    if (disposed) return
    error.value = e instanceof Error ? e.message : '查询失败。'
    if (e instanceof ApiError && e.status === 401) sessionReady = false
    const transient =
      !(e instanceof ApiError) || [0, 408, 429].includes(e.status) || e.status >= 500
    const delay = retryDelays[failures++]
    if (sessionReady && transient && delay !== undefined) {
      retryHint.value = `${delay / 1000} 秒后重新查询；不会重复启动审查。`
      schedule(delay)
    } else {
      retryHint.value = '自动查询已暂停，请点击重新查询。'
    }
  } finally {
    querying.value = false
  }
}
function refresh() {
  if (querying.value || busy.value) return
  failures = 0
  void load()
}
onMounted(() => void load())
onUnmounted(() => {
  disposed = true
  clearTimeout(timer)
  activeRequest?.abort(new DOMException('组件已卸载', 'AbortError'))
})
async function start() {
  if (disposed || busy.value || querying.value || value.value?.state !== 'idle') return
  clearTimeout(timer)
  busy.value = true
  error.value = ''
  startError.value = ''
  retryHint.value = ''
  value.value = { state: 'running' }
  try {
    await request((signal) => management(endpoint() + '/start', {}, signal))
  } catch (e) {
    if (!disposed) {
      startError.value = e instanceof Error ? e.message : '启动结果未知，请刷新查询。'
    }
  } finally {
    busy.value = false
  }
  failures = 0
  await load()
}
</script>
<template>
  <section class="panel section-gap">
    <h2>MCP 只读审查</h2>
    <p v-if="startError" class="notice" role="alert">{{ startError }}</p>
    <p v-if="error" class="notice" role="alert">{{ error }}</p>
    <p v-if="retryHint" class="notice">{{ retryHint }}</p>
    <template v-if="value">
      <button
        class="button primary"
        v-if="value.state === 'idle'"
        :disabled="busy || querying"
        @click="start"
      >
        开始 MCP 审查
      </button>
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
    <button class="button" :disabled="busy || querying" @click="refresh">
      {{ querying ? '查询中…' : '重新查询' }}
    </button>
  </section>
</template>
<style scoped>
.review-report {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
}
button {
  margin-top: 16px;
}
ul {
  max-height: 360px;
  overflow-y: auto;
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.9;
  padding-left: 20px;
}
</style>
