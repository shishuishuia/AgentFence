<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { createReadonlyTask, createEditTask, getAgents, startTaskReview, type AgentType } from '@/api/workspaces'
const props = defineProps<{ workspaceId: string }>()
const router = useRouter()
const READONLY_GOAL = '检查项目中的代码问题，并提出带路径和行号的修改建议。'
const EDIT_GOAL = '修复项目中最影响功能的一个问题，保持其余行为不变，并说明修改原因与影响范围。'
const goal = ref(READONLY_GOAL)
const profile = ref<'readonly' | 'controlled_edit'>('readonly')
const agent = ref<AgentType>('deepseek_harness')
const agents = ref<{ builtin: boolean; deepseek_harness: boolean; builtin_mode: 'unknown' | 'offline' | 'deepseek' } | null>(null)
const busy = ref(false)
const consumed = ref(false)
const error = ref('')
const availabilityError = ref('')
const createdTaskId = ref('')
const controller = new AbortController()
onMounted(async () => {
  try {
    agents.value = await getAgents(controller.signal)
    // External Agent is the primary read-only executor; the built-in offline
    // replay stays available only as a断网 fallback.
    agent.value = agents.value.deepseek_harness ? 'deepseek_harness' : 'builtin'
  } catch (e) {
    if (!controller.signal.aborted) availabilityError.value = e instanceof Error ? e.message : '无法获取 Agent 状态。'
  }
})
watch(profile, (value) => {
  // The external Harness handles both read-only review and isolated edits.
  if (agents.value?.deepseek_harness) agent.value = 'deepseek_harness'
  else if (value === 'controlled_edit') agent.value = 'builtin'
  // Swap the starter goal only while the field still holds a starter goal.
  if (goal.value === READONLY_GOAL || goal.value === EDIT_GOAL)
    goal.value = value === 'controlled_edit' ? EDIT_GOAL : READONLY_GOAL
})
onUnmounted(() => controller.abort())
function runnable() {
  if (!agents.value) return false
  if (profile.value === 'controlled_edit')
    return Boolean(
      agents.value.deepseek_harness ||
        (agents.value.builtin && agents.value.builtin_mode !== 'offline'),
    )
  return agents.value.deepseek_harness || (agents.value.builtin && agents.value.builtin_mode === 'offline')
}
async function submit() {
  if (busy.value || consumed.value || !goal.value.trim() || !runnable()) return
  busy.value = true
  consumed.value = true
  error.value = ''
  try {
    const task = profile.value === 'controlled_edit'
      ? await createEditTask(props.workspaceId, goal.value.trim(), controller.signal)
      : await createReadonlyTask(props.workspaceId, goal.value.trim(), controller.signal)
    createdTaskId.value = task.task_id
    await startTaskReview(task.task_id, agent.value, controller.signal)
    if (!controller.signal.aborted) await router.push(`/tasks/${encodeURIComponent(task.task_id)}`)
  } catch (e) {
    if (!controller.signal.aborted) error.value = e instanceof Error ? e.message : '操作失败。'
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <form @submit.prevent="submit">
    <fieldset :disabled="busy || consumed">
      <legend>任务类型</legend>
      <label><input type="radio" v-model="profile" value="readonly" /> 只读审查</label>
      <label
        ><input
          type="radio"
          v-model="profile"
          value="controlled_edit"
          :disabled="agents?.builtin_mode === 'offline' && !agents?.deepseek_harness"
        />
        读写修改（隔离副本）</label
      >
      <p v-if="agents?.builtin_mode === 'offline' && !agents?.deepseek_harness">
        读写修改需要在线模型或已配置的外部 Agent。
      </p>
      <p v-if="profile === 'controlled_edit'">模型只能修改隔离副本中的已授权现有文件；完成后逐项预览并确认应用到原项目。需要在线模型。</p>
    </fieldset>
    <fieldset :disabled="busy || consumed || !agents">
      <legend>执行方式</legend>
      <template v-if="profile === 'controlled_edit'">
        <label v-if="agents?.deepseek_harness"
          ><input type="radio" v-model="agent" value="deepseek_harness" /> 外部 DeepSeek Harness（修改隔离副本）</label
        >
        <label v-if="agents?.builtin && agents.builtin_mode !== 'offline'"
          ><input type="radio" v-model="agent" value="builtin" /> 内置执行器（DeepSeek）</label
        >
        <p v-if="agents?.builtin_mode === 'offline' && !agents?.deepseek_harness" role="alert">
          读写修改需要在线模型：请先在页面下方保存模型凭证并重启服务。
        </p>
        <p v-else>模型只能修改隔离副本中的已授权现有文件；完成后逐项预览并确认应用到原项目。</p>
      </template>
      <template v-else>
        <label v-if="agents?.deepseek_harness"
          ><input type="radio" v-model="agent" value="deepseek_harness" /> 外部 DeepSeek Harness（读取只读快照）</label
        >
        <label v-if="agents?.builtin_mode === 'offline'"
          ><input type="radio" v-model="agent" value="builtin" /> 离线演示（内置，断网回退）</label
        >
        <p v-if="agents && !agents.deepseek_harness && agents.builtin_mode !== 'offline'" role="alert">
          外部 DeepSeek Harness 尚未配置：AgentFence 只提供围栏，需要外部 Agent 执行只读任务。
        </p>
      </template>
    </fieldset>
    <p v-if="availabilityError" role="alert">无法获取 Agent 可用状态：{{ availabilityError }} 请检查服务版本或连接。</p>
    <label
      >任务目标
      <textarea v-model="goal" maxlength="1000" :disabled="busy || consumed" rows="3" />
    </label>
    <button class="button primary" type="submit" :disabled="busy || consumed || !goal.trim() || !runnable()">
      {{ busy ? '正在创建并启动…' : '创建并启动任务' }}
    </button>
    <p v-if="error" role="alert">{{ error }} {{ createdTaskId ? '任务已创建，启动结果可能未知，请在任务详情中查询。' : '创建结果不确定时，请先检查任务列表。' }}</p>
    <RouterLink v-if="createdTaskId && error" :to="`/tasks/${encodeURIComponent(createdTaskId)}`">查看已创建的任务</RouterLink>
  </form>
</template>
<style scoped>
form {
  margin-top: 20px;
}
fieldset {
  margin: 0 0 18px;
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
label {
  display: block;
  font-size: 13px;
}
textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  font: inherit;
  line-height: 1.8;
  padding: 12px;
  border: 1px solid #c9d8c3;
  border-radius: 8px;
  color: #344d3b;
  margin: 8px 0 12px;
}
</style>
