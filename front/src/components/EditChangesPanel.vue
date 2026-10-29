<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { management, openManagement } from '@/api/workspaces'
const props = defineProps<{ taskId: string }>()
type Entry = { path: string; before_sha256: string; after_sha256: string }
type Detail = Entry & { before: string; after: string }
const entries = ref<Entry[]>([])
const selected = ref<Detail | null>(null)
const accepted = ref(false)
const busy = ref(false)
const message = ref('')
const applied = ref<string[]>([])
const prefix = `/tasks/${encodeURIComponent(props.taskId)}`
async function refresh() {
  busy.value = true; message.value = ''
  try {
    await openManagement()
    const result = await management<{changes: Entry[]}>(`${prefix}/changes`)
    if (!Array.isArray(result.changes)) throw new Error('修改清单无效。')
    entries.value = result.changes
  } catch (error) { message.value = error instanceof Error ? error.message : '无法读取修改清单。' }
  finally { busy.value = false }
}
async function preview(entry: Entry) {
  busy.value = true; message.value = ''; accepted.value = false; selected.value = null
  try {
    const detail = await management<Detail>(`${prefix}/change?path=${encodeURIComponent(entry.path)}`)
    if (detail.path !== entry.path || detail.after_sha256 !== entry.after_sha256 || typeof detail.before !== 'string' || typeof detail.after !== 'string') throw new Error('修改内容已变化，请刷新清单。')
    selected.value = detail
  } catch (error) { message.value = error instanceof Error ? error.message : '无法加载文件差异。' }
  finally { busy.value = false }
}
async function apply() {
  const current = selected.value
  if (!current || !accepted.value || busy.value) return
  accepted.value = false; busy.value = true; message.value = ''
  try {
    await management(`${prefix}/apply`, {path: current.path, after_sha256: current.after_sha256})
    applied.value.push(current.path); selected.value = null
    message.value = '已将确认的文件修改应用到原项目。'
  } catch (error) { message.value = error instanceof Error ? error.message : '应用结果未知，请检查原文件，切勿重复提交。' }
  finally { busy.value = false }
}
onMounted(() => void refresh())
</script>
<template>
  <section class="panel section-gap">
    <h2>修改预览与应用</h2>
    <p>以下修改位于隔离副本。逐个查看原内容和修改后内容，再决定是否应用到原项目。</p>
    <p v-if="message" role="status">{{ message }}</p>
    <p v-if="!entries.length && !busy">没有检测到文件内容变化。</p>
    <ul>
      <li v-for="entry in entries" :key="entry.path">
        <code>{{ entry.path }}</code>
        <span v-if="applied.includes(entry.path)"> · 已应用</span>
        <button v-else class="button" :disabled="busy" @click="preview(entry)">查看修改</button>
      </li>
    </ul>
    <template v-if="selected">
      <h3>{{ selected.path }}</h3>
      <p>原始内容</p><pre>{{ selected.before }}</pre>
      <p>修改后内容</p><pre>{{ selected.after }}</pre>
      <label><input v-model="accepted" type="checkbox" /> 我已检查此文件内容，同意将本次修改应用到原项目。</label>
      <button class="button primary" :disabled="!accepted || busy" @click="apply">确认应用到原项目</button>
      <p>如果原文件在导入后已变化，系统会拒绝覆盖；发生错误或超时后请先检查原文件。</p>
    </template>
    <button class="button" :disabled="busy" @click="refresh">刷新修改清单</button>
  </section>
</template>
<style scoped>
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 400px; overflow: auto; background: #f3f6f0; padding: 12px; }
li { margin: 10px 0; overflow-wrap: anywhere; }
label { display: block; margin: 16px 0; }
</style>
