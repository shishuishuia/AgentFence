<script setup lang="ts">
import { computed, nextTick, onScopeDispose, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { controlDemo, getDemoState } from '@/api/demo'
import { usePolling } from '@/composables/usePolling'

const poll = usePolling((context) => getDemoState(context.signal))
const state = poll.data
const pending = ref(false)
const controlError = ref('')
const task = ref('读取 src/main.rs，把程序输出改为“你好，AgentFence！”，然后读回确认修改正确。')
const isLLM = computed(() => state.value?.mode === 'llm')
const stepsList = ref<HTMLOListElement | null>(null)
const active = computed(() => ['running', 'paused'].includes(state.value?.status ?? ''))
const titles: Record<string, string> = {
  create_task: '创建任务，进入 inspect',
  inspect_read: '读取目标文件',
  inspect_write_denied: '验证 inspect 阶段拒绝写入',
  advance_edit: '推进到 edit 阶段',
  issue_grant: '申请一次性写入租约',
  edit_write: '修改文件并消费一次租约',
  reuse_denied: '验证租约不能重复使用',
  outside_read_denied: '验证区外路径被拦截',
  advance_verify: '推进到 verify 阶段',
  verify_read: '读回并核对修改结果',
  verify_write_denied: '验证 verify 阶段拒绝写入',
  advance_done: '完成任务，收回权限',
  done_read_denied: '验证 done 阶段拒绝读取',
  read_file: 'Agent 调用 read_file',
  write_file: 'Agent 调用 write_file',
}
const statusText = computed(
  () =>
    ({
      idle: '等待开始',
      running: '执行中',
      paused: '已请求暂停',
      succeeded: '执行及核验通过',
      failed: '执行失败',
    })[state.value?.status ?? 'idle'],
)
let controller: AbortController | undefined
let startKey: string | null = null
let previousRun: string | null = null
let disposed = false
watch(
  () => state.value?.run_id,
  (id) => {
    if (id && id !== previousRun) {
      startKey = null
      if (state.value?.task_prompt) task.value = state.value.task_prompt
    }
  },
)
watch(
  () => state.value?.completed_steps,
  async () => {
    await nextTick()
    if (stepsList.value) stepsList.value.scrollTop = stepsList.value.scrollHeight
  },
)

async function act(action: 'start' | 'pause' | 'resume') {
  if (pending.value || !state.value?.enabled) return
  pending.value = true
  controlError.value = ''
  controller = new AbortController()
  const current = controller
  const timer = setTimeout(() => current.abort(), 6000)
  if (action === 'start' && !startKey) {
    startKey = crypto.randomUUID()
    previousRun = state.value.run_id
  }
  try {
    await controlDemo(
      action,
      action === 'start' ? startKey! : state.value.run_id!,
      current.signal,
      action === 'start' && isLLM.value ? task.value : undefined,
    )
    if (disposed) return
    if (action === 'start') startKey = null
    await poll.refresh()
  } catch (error) {
    if (!disposed) {
      controlError.value = current.signal.aborted
        ? '响应超时，执行结果可能未知。正在查询状态，请勿重复启动。'
        : error instanceof Error
          ? error.message
          : '无法控制演示任务。'
      void poll.refresh()
    }
  } finally {
    clearTimeout(timer)
    if (!disposed) pending.value = false
  }
}
onScopeDispose(() => {
  disposed = true
  controller?.abort()
})
</script>

<template>
  <section class="panel live-demo section-gap" aria-labelledby="live-demo-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">LIVE AGENT TASK</p>
        <h2 id="live-demo-title">现场执行一个 Agent 任务</h2>
      </div>
      <span class="result-badge" :class="state?.status === 'failed' ? 'failed' : 'allowed'">{{
        statusText
      }}</span>
    </div>
    <template v-if="isLLM">
      <p class="scope-note">
        真实模型 Agent · {{ state?.provider }} / {{ state?.model }} · 工具调用由 Rust 网关鉴权
      </p>
      <label class="task-input-label" for="agent-task">本次任务</label>
      <textarea
        id="agent-task"
        v-model="task"
        :disabled="active || pending"
        maxlength="1000"
        rows="3"
      />
      <p class="scope-note">
        可读取 src/main.rs、Cargo.toml；可申请一次 src/main.rs
        写入。模型根据你的要求选择工具，完成后读回并核对审计。
      </p>
      <p v-if="state?.model_configured === false" role="alert" class="field-error">
        服务端未配置 DeepSeek 密钥，无法启动模型任务。
      </p>
    </template>
    <p v-else>
      任务：将独立演示工作区中 <code>src/main.rs</code> 的输出从 before 改为 fixed
      中文，读回验证，并检查越权操作是否被拦截。
    </p>
    <p v-if="!isLLM" class="scope-note">
      确定性演示 Agent · 无需模型密钥 · 所有工具调用经过真实 Rust 服务
    </p>
    <div class="demo-actions">
      <button
        class="button primary"
        :disabled="
          !state?.enabled ||
          active ||
          pending ||
          !!poll.error.value ||
          (isLLM && (!task.trim() || state?.model_configured === false))
        "
        @click="act('start')"
      >
        {{
          pending
            ? '提交中…'
            : active
              ? '任务执行中'
              : state?.run_id
                ? '再运行一次'
                : '开始执行任务'
        }}
      </button>
      <button
        v-if="active"
        class="button"
        :disabled="pending || !!poll.error.value"
        @click="act(state?.status === 'paused' ? 'resume' : 'pause')"
      >
        {{ state?.status === 'paused' ? '继续执行' : '暂停演示' }}
      </button>
      <button class="button" :disabled="poll.loading.value" @click="poll.refresh">
        刷新执行状态
      </button>
      <RouterLink v-if="state?.task_id" class="text-link" :to="`/tasks/${state.task_id}`"
        >查看任务与租约 →</RouterLink
      >
      <RouterLink v-if="state?.task_id" class="text-link" :to="`/audit?taskId=${state.task_id}`"
        >查看本次审计 →</RouterLink
      >
    </div>
    <p v-if="state && !state.enabled" class="notice">
      当前服务为只读展示。交互演示需使用终端入口
      <code>python -m demo.run_live_dashboard --mode dev</code>。
    </p>
    <p v-if="poll.error.value || controlError" role="alert" class="field-error">
      {{ controlError || poll.error.value?.message }}
    </p>
    <template v-if="state?.run_id">
      <div class="demo-progress-label" aria-live="polite">
        <span v-if="isLLM"
          >{{ state.phase || '准备中' }} · {{ state.completed_steps }} 条执行记录 · 模型第
          {{ state.model_round }} / {{ state.max_rounds }} 轮</span
        >
        <span v-else
          >{{ state.phase || '准备中' }} · {{ state.completed_steps }} /
          {{ state.total_steps }} 步</span
        >
        <span>{{
          state.verified
            ? '文件与 API / 磁盘审计核验通过'
            : '步骤显示已核对的 HTTP 响应，结束后核验文件和审计'
        }}</span>
      </div>
      <progress
        v-if="state.total_steps !== null"
        :value="state.completed_steps"
        :max="state.total_steps"
        aria-label="Agent 任务进度"
      />
      <p v-if="state.status === 'paused'" class="scope-note">
        当前请求完成后暂停。租约仍按真实时间到期，请及时继续。
      </p>
      <p v-if="state.failure" role="alert" class="field-error">{{ state.failure }}</p>
      <p v-if="isLLM" aria-live="polite">
        {{ state.activity
        }}<span v-if="state.usage"> · 累计 {{ state.usage.total_tokens }} tokens</span>
      </p>
      <details v-if="state.model_messages?.length" class="model-replies">
        <summary>模型执行说明（{{ state.model_messages.length }}）</summary>
        <p v-for="(message, index) in state.model_messages" :key="index">{{ message }}</p>
      </details>
      <p v-if="state.summary" class="model-summary">{{ state.summary }}</p>
      <ol ref="stepsList" class="demo-steps" aria-label="实时执行记录">
        <li v-for="(step, index) in state.steps" :key="index">
          <span>{{ titles[step.case] || step.case }}</span>
          <span
            class="result-badge"
            :class="
              isLLM && step.http_status === 403 ? 'denied' : !step.pass ? 'failed' : 'allowed'
            "
            >{{
              isLLM && step.http_status === 403
                ? '策略拒绝'
                : !step.pass
                  ? '非预期响应'
                  : step.http_status === 403
                    ? '预期拦截'
                    : '成功'
            }}
            · {{ step.http_status }}</span
          >
          <code>{{ step.reason_code || 'OK' }}</code>
        </li>
      </ol>
    </template>
  </section>
</template>

<style scoped>
.live-demo {
  background: linear-gradient(120deg, #f0f5ec, #fff);
  margin-bottom: 24px;
}
.task-input-label {
  display: block;
  font-size: 13px;
  margin: 16px 0 10px;
}
textarea {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  font: inherit;
  line-height: 1.8;
  padding: 12px;
  border: 1px solid #c9d8c3;
  border-radius: 8px;
  color: #344d3b;
  background: #fff;
}
.model-replies,
.model-summary {
  margin-top: 18px;
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
}
.demo-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.demo-progress-label {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  margin-top: 24px;
  font-size: 12px;
  color: #486b54;
}
progress {
  width: 100%;
  height: 10px;
  margin-top: 12px;
  accent-color: #426b4b;
}
.demo-steps {
  display: grid;
  gap: 10px;
  padding-left: 22px;
  font-size: 12px;
  max-height: 320px;
  overflow-y: auto;
}
.demo-steps li {
  padding: 9px 0;
  line-height: 1.9;
}
.demo-steps .result-badge {
  margin: 0 12px;
}
.demo-steps code {
  color: #718374;
}
button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
