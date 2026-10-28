<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import {
  openManagement,
  startPicker,
  getSelection,
  confirmWorkspace,
  getWorkspaces,
  getExclusions,
  type Selection,
  type Workspace,
} from '@/api/workspaces'
import { formatTime } from '@/utils/audit'
import ReadonlyTaskForm from '@/components/ReadonlyTaskForm.vue'

const selection = ref<Selection | null>(null)
const workspaces = ref<Workspace[]>([])
const name = ref('')
const busy = ref(false)
const connected = ref(false)
const error = ref('')
const controller = new AbortController()
let timer: ReturnType<typeof setTimeout> | undefined
const message = (e: unknown) => (e instanceof Error ? e.message : '管理操作失败。')
async function refresh() {
  workspaces.value = (await getWorkspaces(controller.signal)).items
}
onMounted(async () => {
  try {
    await openManagement(controller.signal)
    connected.value = true
    await refresh()
  } catch (e) {
    if (!controller.signal.aborted) error.value = message(e)
  }
})
onUnmounted(() => {
  controller.abort()
  clearTimeout(timer)
})
async function poll(id: string) {
  try {
    const result = await getSelection(id, controller.signal)
    if (controller.signal.aborted) return
    selection.value = result
    if (result.state === 'pending') timer = setTimeout(() => void poll(id), 500)
    else {
      busy.value = false
      if (result.state === 'failed') error.value = result.message ?? '预览失败，请重新选择。'
    }
  } catch (e) {
    if (!controller.signal.aborted) {
      error.value = message(e)
      busy.value = false
    }
  }
}
async function choose() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  selection.value = null
  try {
    const result = await startPicker(controller.signal)
    selection.value = result
    await poll(result.selection_id)
  } catch (e) {
    if (!controller.signal.aborted) error.value = message(e)
    busy.value = false
  }
}
async function confirm() {
  if (busy.value || selection.value?.state !== 'ready' || !name.value.trim()) return
  busy.value = true
  error.value = ''
  const id = selection.value.selection_id
  // Any uncertain import outcome consumes the selection. Never offer a retry.
  selection.value = { selection_id: id, state: 'consumed' }
  try {
    await confirmWorkspace(id, name.value.trim(), controller.signal)
    await refresh()
  } catch (e) {
    if (!controller.signal.aborted) error.value = message(e)
  } finally {
    busy.value = false
  }
}
async function loadExcluded() {
  const current = selection.value
  if (!current?.preview || current.state !== 'ready' || busy.value) return
  busy.value = true
  error.value = ''
  try {
    for (
      let page = 0;
      page < 100 && current.preview.excluded.length < current.preview.excluded_file_count;
      page++
    ) {
      const result = await getExclusions(
        current.selection_id,
        current.preview.excluded.length,
        controller.signal,
      )
      current.preview.excluded.push(...result.items)
      if (result.next_offset === null) break
      if (result.next_offset !== current.preview.excluded.length)
        throw new Error('排除项分页不连续，请重新选择目录。')
    }
  } catch (e) {
    if (!controller.signal.aborted) error.value = message(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section>
    <p class="eyebrow">READONLY WORKSPACES</p>
    <h1>工作区管理</h1>
    <p class="page-description">选择本机项目，预览后导入只读快照。审查使用导入时的副本。</p>
    <p v-if="error" class="notice" role="alert">{{ error }}</p>
    <section class="panel">
      <h2>导入项目</h2>
      <button class="button primary" :disabled="busy || !connected" @click="choose">
        {{ busy ? '正在处理…' : '选择本机文件夹' }}
      </button>
      <p v-if="selection?.state === 'pending'" role="status">
        请完成系统目录选择，随后等待文件预览。
      </p>
      <p v-if="selection?.state === 'cancelled'" role="status">已取消，未注册工作区。</p>
      <p v-if="selection?.state === 'consumed'">本次选择已使用。如需再次导入，请重新选择目录。</p>
      <template v-if="selection?.state === 'ready' && selection.preview">
        <p class="mono">源位置（仅管理端）：{{ selection.display_path }}</p>
        <p>
          允许 {{ selection.preview.allowed_file_count }} 个文件，排除
          {{ selection.preview.excluded_file_count }} 项，共
          {{ selection.preview.total_bytes }} 字节。
        </p>
        <p>预览有效期至 {{ formatTime(selection.preview.expires_at) }}；源文件变化需要重新预览。</p>
        <details>
          <summary>允许导入的文件</summary>
          <ul>
            <li v-for="file in selection.preview.files" :key="file.path">
              {{ file.path }} · {{ file.bytes }} 字节
            </li>
          </ul>
        </details>
        <details>
          <summary>排除项</summary>
          <ul>
            <li v-for="item in selection.preview.excluded" :key="item.path">
              {{ item.path }}：{{ item.reason_code }}
            </li>
          </ul>
        </details>
        <button
          class="button"
          v-if="selection.preview.excluded.length < selection.preview.excluded_file_count"
          :disabled="busy"
          @click="loadExcluded"
        >
          加载全部排除项（已加载 {{ selection.preview.excluded.length }} /
          {{ selection.preview.excluded_file_count }}）
        </button>
        <label
          >工作区名称 <input v-model="name" maxlength="80" placeholder="例如：示例 Rust 项目"
        /></label>
        <button
          class="button primary"
          :disabled="
            busy ||
            !name.trim() ||
            selection.preview.excluded.length < selection.preview.excluded_file_count
          "
          @click="confirm"
        >
          确认导入只读快照
        </button>
      </template>
    </section>
    <section class="panel section-gap">
      <h2>已导入工作区</h2>
      <p v-if="!workspaces.length">暂无已导入工作区。</p>
      <article
        v-for="workspace in workspaces"
        :key="workspace.workspace_id"
        class="panel section-gap"
      >
        <h3>{{ workspace.display_name }}</h3>
        <p>
          只读快照 · {{ workspace.allowed_file_count }} 个文件 ·
          {{ formatTime(workspace.imported_at) }}
        </p>
        <p class="mono">{{ workspace.workspace_id }} / {{ workspace.snapshot_id }}</p>
        <p class="mono">清单 SHA-256：{{ workspace.manifest_sha256 }}</p>
        <ReadonlyTaskForm :workspace-id="workspace.workspace_id" />
      </article>
    </section>
  </section>
</template>
<style scoped>
.panel > button {
  margin-top: 16px;
}
.mono {
  overflow-wrap: anywhere;
}
details {
  margin: 16px 0;
  font-size: 13px;
}
details ul {
  max-height: 320px;
  overflow-y: auto;
  padding-left: 20px;
  overflow-wrap: anywhere;
  line-height: 1.9;
}
label {
  display: block;
  margin-top: 20px;
  font-size: 13px;
}
input {
  display: block;
  width: min(100%, 360px);
  margin-top: 8px;
  box-sizing: border-box;
}
</style>
