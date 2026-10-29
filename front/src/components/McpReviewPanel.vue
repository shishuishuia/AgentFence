<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { shortRequest } from '@/utils/audit'
import { openManagement, management } from '@/api/workspaces'
import { ApiError } from '@/api/client'
const props = defineProps<{ taskId: string; editable?: boolean }>()
const emit = defineEmits<{ state: [string] }>()
interface Review {
  state: 'idle' | 'running' | 'completed' | 'failed'
  call_count?: number
  read_file_count?: number
  mode?: string
  agent_type?: string
  progress_text?: string
  report?: string
  message?: string
  started_at?: string | null
  finished_at?: string | null
  calls?: { tool: string; request_id?: string; ok: boolean; path?: string }[]
}
const value = ref<Review | null>(null)
const error = ref('')
const startError = ref('')
const busy = ref(false)
const querying = ref(false)
const retryHint = ref('')
const selectedAgent = ref<'builtin' | 'deepseek_harness'>('builtin')
let requestedHarness = false
const retryDelays = [1000, 2000, 4000]
let failures = 0
let sessionReady = false
let disposed = false
let activeRequest: AbortController | undefined
let timer: ReturnType<typeof setTimeout> | undefined
const endpoint = () => `/tasks/${encodeURIComponent(props.taskId)}/review`
const tick = ref(0)
let clock: ReturnType<typeof setInterval> | undefined
function startClock() {
  if (!clock) clock = setInterval(() => (tick.value += 1), 1000)
}
function stopClock() {
  if (clock) clearInterval(clock)
  clock = undefined
}
watch(
  () => value.value?.state,
  (state) => (state === 'running' ? startClock() : stopClock()),
)
watch(
  () => value.value?.state,
  (state) => {
    if (state) emit('state', state)
  },
)
const elapsed = computed(() => {
  void tick.value
  const start = value.value?.started_at ? Date.parse(value.value.started_at) : NaN
  if (!Number.isFinite(start)) return null
  const end = value.value?.finished_at ? Date.parse(value.value.finished_at) : Date.now()
  if (!Number.isFinite(end) || end < start) return null
  return Math.round((end - start) / 1000)
})
function durationText(seconds: number | null) {
  if (seconds === null) return ''
  if (seconds < 60) return `${seconds} 秒`
  return `${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, '0')} 秒`
}
const statusLabel = computed(
  () => ({ idle: '未开始', running: '运行中', completed: '已完成', failed: '失败' })[
    value.value?.state ?? 'idle'
  ],
)
const statusSummary = computed(() => {
  const v = value.value
  if (!v) return ''
  const counts = `读取 ${v.read_file_count || 0} 个文件 · 记录 ${v.call_count || 0} 次调用`
  const time = durationText(elapsed.value)
  if (v.state === 'running')
    return [time ? `已运行 ${time}` : '', counts, v.progress_text].filter(Boolean).join(' · ')
  if (v.state === 'completed') return [time ? `用时 ${time}` : '', counts].filter(Boolean).join(' · ')
  if (v.state === 'failed')
    return [time ? `运行 ${time}` : '', v.message || '原因未知'].filter(Boolean).join(' · ')
  return '尚未开始'
})
const statusNote = computed(() => {
  const state = value.value?.state
  if (state === 'running')
    return props.editable
      ? '外部 Agent 正在读取文件并生成修改；页面每秒刷新，关闭页面不会中断服务端任务。'
      : '外部 Agent 正在读取文件并生成审查建议；页面每秒刷新，关闭页面不会中断服务端任务。'
  if (state === 'completed')
    return props.editable
      ? '运行已结束，副本改动可在下方逐项核对并确认应用。'
      : '运行已结束，报告可人工复核。'
  if (state === 'failed')
    return props.editable
      ? '运行未正常结束；隔离副本可能已被修改，下方差异仍可查看。'
      : '运行未正常结束，请核对原因后重新创建任务。'
  return ''
})

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
    error.value = requestedHarness && result.state !== 'idle' && result.agent_type !== 'deepseek_harness'
      ? '服务端尚未确认使用 DeepSeek Harness，不能将当前运行认定为外部 Agent。'
      : ''
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
  requestedHarness = selectedAgent.value === 'deepseek_harness'
  value.value = { state: 'running' }
  try {
    const body = requestedHarness ? { agent_type: 'deepseek_harness' } : {}
    await request((signal) => management(endpoint() + '/start', body, signal))
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
    <div class="panel-head">
      <h2>{{ editable ? 'MCP 隔离修改' : 'MCP 只读审查' }}</h2>
      <button class="button compact" :disabled="busy || querying" @click="refresh">
        {{ querying ? '查询中…' : '重新查询' }}
      </button>
    </div>
    <p v-if="startError" class="notice" role="alert">{{ startError }}</p>
    <p v-if="error" class="notice" role="alert">{{ error }}</p>
    <p v-if="retryHint" class="notice">{{ retryHint }}</p>
    <template v-if="value">
      <fieldset v-if="value.state === 'idle'" :disabled="busy || querying">
        <legend>执行方式</legend>
        <label><input v-model="selectedAgent" type="radio" value="builtin" /> 内置执行器（DeepSeek）</label>
        <label><input v-model="selectedAgent" type="radio" value="deepseek_harness" /> 外部 DeepSeek Harness</label>
        <p v-if="editable">外部 Harness 只修改隔离副本；确认前不会改动原项目。</p>
        <p v-else-if="selectedAgent === 'deepseek_harness'">使用外部 Agent 需要服务端配置对应运行环境；不可用时会显示错误，不会自动切换执行方式。</p>
      </fieldset>
      <button
        class="button primary"
        v-if="value.state === 'idle'"
        :disabled="busy || querying"
        @click="start"
      >
        {{ editable ? '开始 MCP 修改' : '开始 MCP 审查' }}
      </button>
      <div class="review-status" :class="value.state">
        <span class="state-dot" aria-hidden="true"></span>
        <strong>{{ statusLabel }}</strong>
        <span class="state-summary">{{ statusSummary }}</span>
      </div>
      <p v-if="statusNote" class="state-note" role="status">{{ statusNote }}</p>
      <p v-if="value.agent_type === 'deepseek_harness'" class="review-meta">
        执行方式：外部 DeepSeek Harness
      </p>
      <p v-if="value.mode === 'offline_mcp_replay'" class="notice">
        离线 MCP 调用重放：已核验文件与审计，未调用模型生成审查建议。
      </p>
      <details v-if="value.report" class="report-block" open>
        <summary>审查报告</summary>
        <pre class="review-report">{{ value.report }}</pre>
      </details>
      <details v-if="value.calls?.length" class="calls-block">
        <summary>调用记录（{{ value.calls.length }} 条）</summary>
        <ul>
          <li v-for="(call, index) in value.calls" :key="index">
            <code>{{ call.tool }} {{ call.path || '' }}</code>
            <span>{{ call.ok ? '成功' : '拒绝/失败' }}</span>
            <code :title="call.request_id || ''">{{
              call.request_id ? shortRequest(call.request_id) : '未进入 Broker'
            }}</code>
          </li>
        </ul>
      </details>
    </template>
  </section>
</template>
<style scoped>
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e1e8da;
}
.panel-head .button {
  margin-top: 0;
}
button {
  margin-top: 16px;
}
fieldset {
  margin-top: 16px;
  padding: 12px 16px 16px;
  border: 1px solid #d7e0d1;
  border-radius: 10px;
  background: #fff;
}
fieldset legend {
  padding: 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: #2f5d44;
}
.review-meta {
  margin-top: 10px;
  font-size: 12px;
  color: #5f7266;
}
.report-block,
.calls-block {
  margin-top: 16px;
  border: 1px solid #e1e8da;
  border-radius: 10px;
  background: #fff;
}
.report-block summary,
.calls-block summary {
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 600;
  color: #2f5d44;
  cursor: pointer;
}
.review-report {
  margin: 0;
  padding: 0 14px 14px;
  max-height: 520px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  font-size: 13px;
  line-height: 1.8;
  color: #3f5447;
}
.calls-block ul {
  margin: 0;
  padding: 0 14px 14px 32px;
  max-height: 320px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.9;
}
.review-status {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 16px;
  padding: 10px 14px;
  border: 1px solid #c9d8c3;
  border-radius: 8px;
  background: #f6f9f4;
  flex-wrap: wrap;
}
.review-status.running { border-color: #d9b45a; background: #fdf7e6; }
.review-status.completed { border-color: #7fb98a; background: #eef7ef; }
.review-status.failed { border-color: #d08a86; background: #fdf0ef; }
.state-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #8a9a8f;
  flex: none;
}
.running .state-dot { background: #c98a12; animation: state-pulse 1.2s ease-in-out infinite; }
.completed .state-dot { background: #3f8f4f; }
.failed .state-dot { background: #b3413a; }
.state-summary { font-size: 13px; opacity: 0.85; }
.state-note { margin-top: 8px; font-size: 13px; }
@keyframes state-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.35; transform: scale(0.75); }
}
fieldset label { display: block; margin: 8px 0; }
ul {
  max-height: 360px;
  overflow-y: auto;
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.9;
  padding-left: 20px;
}
</style>
