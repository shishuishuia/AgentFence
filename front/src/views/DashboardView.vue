<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { getHealth, getTasks } from '@/api'
import { usePolling } from '@/composables/usePolling'
import { useAuditEvents } from '@/composables/useAuditEvents'
import { useBrokerSession } from '@/composables/useBrokerSession'
import { statistics } from '@/utils/audit'
import RequestState from '@/components/RequestState.vue'
import TaskTable from '@/components/TaskTable.vue'
import LiveDemoPanel from '@/components/LiveDemoPanel.vue'
const session = useBrokerSession()
const health = usePolling((context) => getHealth(context.signal))
const tasks = usePolling((context) => getTasks(5, context.signal))
const feed = useAuditEvents()
const audit = usePolling((context) => feed.load(context), { reset: feed.reset, fatal: feed.fatal })
const counts = computed(() => statistics(feed.events.value))
</script>
<template>
  <section>
    <p class="eyebrow">AGENTFENCE WORKSPACE</p>
    <h1>项目总览</h1>
    <p class="page-description">从真实任务与审计结果，观察阶段如何约束工具权限。</p>
    <section class="panel connection-panel">
      <div class="section-heading">
        <h2>服务连接</h2>
        <span
          class="status-label"
          :class="health.error.value ? 'failed' : health.data.value ? 'connected' : 'loading'"
          >{{ health.error.value ? '未连接' : health.data.value ? '已连接' : '检测中' }}</span
        >
      </div>
      <p class="mono">当前实例：{{ session.instanceId.value || '等待查询' }}</p>
      <RequestState
        :loading="health.loading.value"
        :error="health.error.value"
        :updated-at="health.updatedAt.value"
        :has-data="!!health.data.value"
        @refresh="health.refresh"
      />
      <p v-if="health.error.value">健康异常时，仍可查询已有任务与审计以排查问题。</p>
    </section>
    <LiveDemoPanel />
    <div class="metric-grid">
      <section class="panel">
        <span>执行成功</span><strong>{{ counts.allowed }}</strong>
      </section>
      <section class="panel">
        <span>策略拒绝</span><strong>{{ counts.denied }}</strong>
      </section>
      <section class="panel">
        <span>执行或输入失败</span><strong>{{ counts.failed }}</strong>
      </section>
    </div>
    <RequestState
      :loading="audit.loading.value"
      :error="audit.error.value"
      :updated-at="audit.updatedAt.value"
      :has-data="!!audit.data.value"
      @refresh="audit.reset"
    />
    <p class="scope-note">
      统计仅针对当前加载的 {{ feed.events.value.length }} 条事件中的工具结果。<template
        v-if="feed.truncated.value"
        >仅保留最近 2000 条。</template
      ><template v-if="feed.hasMore.value">正在继续加载。</template
      >管理事件和调用意图不计入完成结果。
    </p>
    <section class="panel section-gap">
      <div class="section-heading">
        <h2>最近任务</h2>
        <RouterLink class="text-link" to="/tasks">查看任务列表 →</RouterLink>
      </div>
      <RequestState
        :loading="tasks.loading.value"
        :error="tasks.error.value"
        :updated-at="tasks.updatedAt.value"
        :has-data="!!tasks.data.value"
        @refresh="tasks.refresh"
      />
      <p v-if="tasks.data.value?.truncated" class="scope-note">
        仅展示最近 5 个任务；当前实例共 {{ tasks.data.value.total }} 个。
      </p>
      <TaskTable v-if="tasks.data.value" :tasks="tasks.data.value.items" />
    </section>
  </section>
</template>
