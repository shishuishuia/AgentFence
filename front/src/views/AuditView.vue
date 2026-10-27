<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/client'
import { usePolling } from '@/composables/usePolling'
import { useAuditEvents } from '@/composables/useAuditEvents'
import RequestState from '@/components/RequestState.vue'
import AuditEventTable from '@/components/AuditEventTable.vue'
import type { ResultFilter } from '@/utils/audit'
const route = useRoute()
const router = useRouter()
const taskId = computed(() => (typeof route.query.taskId === 'string' ? route.query.taskId : ''))
const result = computed<ResultFilter>(() =>
  ['allowed', 'denied', 'failed'].includes(String(route.query.result))
    ? (route.query.result as ResultFilter)
    : 'all',
)
const inputTask = ref(taskId.value)
const inputResult = ref<ResultFilter>(result.value)
const validation = ref('')
watch([taskId, result], () => {
  inputTask.value = taskId.value
  inputResult.value = result.value
  validation.value = ''
})
function apply() {
  const value = inputTask.value.trim()
  if (value && !/^[A-Za-z0-9_-]{1,128}$/.test(value)) {
    validation.value = '任务 ID 只允许 1～128 个字母、数字、下划线或连字符。'
    return
  }
  validation.value = ''
  void router.push({
    path: '/audit',
    query: {
      ...(value ? { taskId: value } : {}),
      ...(inputResult.value === 'all' ? {} : { result: inputResult.value }),
    },
  })
}
const feed = useAuditEvents()
const audit = usePolling(
  (context) => {
    if (taskId.value && !/^[A-Za-z0-9_-]{1,128}$/.test(taskId.value))
      throw new ApiError(400, 'INVALID_REQUEST', '路由中的任务 ID 格式不正确。')
    return feed.load(context, taskId.value || undefined)
  },
  { key: taskId, reset: feed.reset, fatal: feed.fatal },
)
</script>
<template>
  <section>
    <p class="eyebrow">AUDIT TRAIL</p>
    <h1>审计事件</h1>
    <p class="page-description">观察工具调用与权限决策，点击请求 ID 关联调用意图和结果。</p>
    <form class="panel filter-bar" @submit.prevent="apply">
      <label>任务 ID<input v-model="inputTask" placeholder="留空查询当前实例全部任务" /></label
      ><label
        >结果<select v-model="inputResult">
          <option value="all">全部事件</option>
          <option value="allowed">执行成功</option>
          <option value="denied">策略拒绝</option>
          <option value="failed">执行或输入失败</option>
        </select></label
      ><button class="button primary" type="submit">应用筛选</button>
      <p v-if="validation" class="field-error">{{ validation }}</p>
    </form>
    <RequestState
      :loading="audit.loading.value"
      :error="audit.error.value"
      :updated-at="audit.updatedAt.value"
      :has-data="!!audit.data.value"
      @refresh="audit.reset"
    />
    <section class="panel">
      <p class="scope-note">
        当前加载范围 {{ feed.events.value.length }} 条；结果筛选仅针对已加载事件。<template
          v-if="feed.truncated.value"
          >仅保留最近 2000 条。</template
        ><template v-if="feed.hasMore.value">正在继续加载。</template>
      </p>
      <AuditEventTable :events="feed.events.value" :filter="result" />
    </section>
  </section>
</template>
