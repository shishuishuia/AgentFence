<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createReadonlyTask } from '@/api/workspaces'
const props = defineProps<{ workspaceId: string }>()
const router = useRouter()
const goal = ref('检查项目中的代码问题，并提出带路径和行号的修改建议。')
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
    const task = await createReadonlyTask(props.workspaceId, goal.value.trim(), controller.signal)
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
    <label
      >只读审查目标
      <textarea v-model="goal" maxlength="1000" :disabled="busy || consumed" rows="3" />
    </label>
    <button type="submit" :disabled="busy || consumed || !goal.trim()">
      {{ busy ? '正在创建…' : '创建只读任务' }}
    </button>
    <p v-if="error" role="alert">{{ error }} 创建结果不确定时，请先检查任务列表。</p>
  </form>
</template>
