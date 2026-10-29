<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createReadonlyTask, createEditTask } from '@/api/workspaces'
const props = defineProps<{ workspaceId: string }>()
const router = useRouter()
const goal = ref('检查项目中的代码问题，并提出带路径和行号的修改建议。')
const profile = ref<'readonly' | 'controlled_edit'>('readonly')
const busy = ref(false)
const consumed = ref(false)
const error = ref('')
const controller = new AbortController()
onUnmounted(() => controller.abort())
async function submit() {
  if (busy.value || consumed.value || !goal.value.trim()) return
  busy.value = true
  consumed.value = true
  error.value = ''
  try {
    const task = profile.value === 'controlled_edit'
      ? await createEditTask(props.workspaceId, goal.value.trim(), controller.signal)
      : await createReadonlyTask(props.workspaceId, goal.value.trim(), controller.signal)
    if (!controller.signal.aborted) await router.push(`/tasks/${encodeURIComponent(task.task_id)}`)
  } catch (e) {
    if (!controller.signal.aborted) error.value = e instanceof Error ? e.message : '创建失败。'
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
      <label><input type="radio" v-model="profile" value="controlled_edit" /> 读写修改（隔离副本）</label>
      <p v-if="profile === 'controlled_edit'">模型只能修改隔离副本中的已授权现有文件；完成后逐项预览并确认应用到原项目。需要在线模型。</p>
    </fieldset>
    <label
      >任务目标
      <textarea v-model="goal" maxlength="1000" :disabled="busy || consumed" rows="3" />
    </label>
    <button class="button primary" type="submit" :disabled="busy || consumed || !goal.trim()">
      {{ busy ? '正在创建…' : profile === 'readonly' ? '创建只读任务' : '创建读写修改任务' }}
    </button>
    <p v-if="error" role="alert">{{ error }} 创建结果不确定时，请先检查任务列表。</p>
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
