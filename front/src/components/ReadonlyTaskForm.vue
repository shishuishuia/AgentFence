<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { createReadonlyTask, createEditTask, getAgents, startTaskReview, type AgentType } from '@/api/workspaces'
const props = defineProps<{ workspaceId: string }>()
const router = useRouter()
const goal = ref('检查项目中的代码问题，并提出带路径和行号的修改建议。')
const profile = ref<'readonly' | 'controlled_edit'>('readonly')
const agent = ref<AgentType>('builtin')
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
    if (!agents.value.builtin && agents.value.deepseek_harness && profile.value === 'readonly') agent.value = 'deepseek_harness'
  } catch (e) {
    if (!controller.signal.aborted) availabilityError.value = e instanceof Error ? e.message : '无法获取 Agent 状态。'
  }
})
watch(profile, (value) => {
  if (value === 'controlled_edit' && agent.value === 'deepseek_harness') agent.value = 'builtin'
})
onUnmounted(() => controller.abort())
async function submit() {
  if (busy.value || consumed.value || !goal.value.trim() || !agents.value?.[agent.value] || (profile.value === 'controlled_edit' && agent.value === 'deepseek_harness')) return
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
      <label><input type="radio" v-model="profile" value="controlled_edit" :disabled="agents?.builtin_mode === 'offline'" /> 读写修改（隔离副本）</label>
      <p v-if="agents?.builtin_mode === 'offline'">离线演示不支持修改任务，请使用在线模型服务。</p>
      <p v-if="profile === 'controlled_edit'">模型只能修改隔离副本中的已授权现有文件；完成后逐项预览并确认应用到原项目。需要在线模型。</p>
    </fieldset>
    <fieldset :disabled="busy || consumed || !agents">
      <legend>选择 Agent</legend>
      <label><input type="radio" v-model="agent" value="builtin" :disabled="!agents?.builtin" /> {{ agents?.builtin_mode === 'deepseek' ? 'DeepSeek（内置）' : agents?.builtin_mode === 'offline' ? '离线演示（内置）' : '当前服务运行器' }}</label>
      <label><input type="radio" v-model="agent" value="deepseek_harness" :disabled="!agents?.deepseek_harness || profile === 'controlled_edit'" /> 外部 DeepSeek Harness</label>
      <p v-if="agents && !agents.deepseek_harness">外部 DeepSeek Harness 尚未配置完成。</p>
      <p v-else-if="profile === 'controlled_edit'">外部 DeepSeek Harness 当前只支持只读任务。</p>
    </fieldset>
    <p v-if="availabilityError" role="alert">无法获取 Agent 可用状态：{{ availabilityError }} 请检查服务版本或连接。</p>
    <label
      >任务目标
      <textarea v-model="goal" maxlength="1000" :disabled="busy || consumed" rows="3" />
    </label>
    <button class="button primary" type="submit" :disabled="busy || consumed || !goal.trim() || !agents?.[agent]">
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
