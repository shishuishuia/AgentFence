<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { getTask } from '@/api'
import { ApiError } from '@/api/client'
import { usePolling } from '@/composables/usePolling'
import { useAuditEvents } from '@/composables/useAuditEvents'
import RequestState from '@/components/RequestState.vue'
import PhaseBadge from '@/components/PhaseBadge.vue'
import AuditEventTable from '@/components/AuditEventTable.vue'
import McpReviewPanel from '@/components/McpReviewPanel.vue'
import { formatTime } from '@/utils/audit'
import type { GrantStatus } from '@/types/api'
const props = defineProps<{ taskId: string }>()
const detail = usePolling((context) => getTask(props.taskId, context.signal), {
  key: () => props.taskId,
  fatal: (error) => error instanceof ApiError && error.status === 404,
})
const feed = useAuditEvents()
const audit = usePolling((context) => feed.load(context, props.taskId), {
  key: () => props.taskId,
  reset: feed.reset,
  fatal: feed.fatal,
})
const statuses: Record<GrantStatus, string> = {
  active: '有效',
  used_up: '次数耗尽',
  expired: '已过期',
  revoked: '已撤销',
  stage_inactive: '当前阶段不可用',
}
</script>
<template>
  <section>
    <p class="eyebrow">TASK DETAIL</p>
    <h1>任务详情</h1>
    <p class="page-description mono">任务标识：{{ taskId }}</p>
    <RouterLink class="text-link" to="/tasks">← 返回任务列表</RouterLink>
    <RequestState
      :loading="detail.loading.value"
      :error="detail.error.value"
      :updated-at="detail.updatedAt.value"
      :has-data="!!detail.data.value"
      @refresh="detail.refresh"
    />
    <p
      v-if="detail.error.value instanceof ApiError && detail.error.value.status === 404"
      class="notice"
    >
      当前实例不存在该任务。服务重启后，旧任务不会恢复。
    </p>
    <template v-if="detail.data.value">
      <McpReviewPanel
        v-if="detail.data.value.permission_profile === 'readonly' || detail.data.value.permission_profile === 'controlled_edit'"
        :key="taskId"
        :task-id="taskId"
        :editable="detail.data.value.permission_profile === 'controlled_edit'"
      />
      <div class="detail-grid">
        <section class="panel">
          <div class="section-heading">
            <h2>任务状态</h2>
            <PhaseBadge :phase="detail.data.value.phase" />
          </div>
          <dl>
            <dt>工作区</dt>
            <dd>{{ detail.data.value.workspace_id }}</dd>
            <template v-if="detail.data.value.permission_profile">
              <dt>任务权限</dt>
              <dd>
                {{
                  detail.data.value.permission_profile === 'readonly'
                    ? '只读（服务端策略）'
                    : '受控编辑'
                }}
              </dd>
              <dt>快照</dt>
              <dd class="mono">{{ detail.data.value.snapshot_id || '—' }}</dd>
              <dt>审查目标</dt>
              <dd>{{ detail.data.value.goal || '—' }}</dd>
            </template>
            <dt>创建时间</dt>
            <dd>{{ formatTime(detail.data.value.created_at) }}</dd>
            <dt>更新时间</dt>
            <dd>{{ formatTime(detail.data.value.updated_at) }}</dd>
          </dl>
        </section>
        <section class="panel">
          <h2>允许访问的相对路径</h2>
          <ul class="path-list">
            <li v-for="path in detail.data.value.allowed_paths" :key="path">
              <code>{{ path }}</code>
            </li>
          </ul>
          <p>路径许可与任务阶段、租约共同决定工具是否可以执行。</p>
        </section>
      </div>
      <section class="panel section-gap">
        <h2>租约快照</h2>
        <p>状态由 Rust 计算。服务端采样时间：{{ formatTime(detail.data.value.server_time) }}</p>
        <p v-if="!detail.data.value.grants.length" class="empty-message">
          {{
            detail.data.value.permission_profile === 'readonly'
              ? '只读任务禁止发放写租约。'
              : '暂无租约。'
          }}
        </p>
        <div v-else class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>租约</th>
                <th>路径</th>
                <th>状态</th>
                <th>剩余次数</th>
                <th>到期时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="grant in detail.data.value.grants" :key="grant.grant_id">
                <td class="mono">{{ grant.grant_id }}</td>
                <td class="mono">{{ grant.path }}</td>
                <td>
                  <span
                    class="result-badge"
                    :class="grant.status === 'active' ? 'allowed' : 'neutral'"
                    >{{ statuses[grant.status] }}</span
                  >
                </td>
                <td>{{ grant.remaining_uses }}</td>
                <td>{{ formatTime(grant.expires_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
    <section class="panel section-gap">
      <div class="section-heading">
        <h2>关联审计</h2>
        <RouterLink class="text-link" :to="{ path: '/audit', query: { taskId } }"
          >前往审计页面 →</RouterLink
        >
      </div>
      <RequestState
        :loading="audit.loading.value"
        :error="audit.error.value"
        :updated-at="audit.updatedAt.value"
        :has-data="!!audit.data.value"
        @refresh="audit.reset"
      />
      <p class="scope-note">
        当前加载 {{ feed.events.value.length }} 条<template v-if="feed.truncated.value">
          · 仅保留最近 2000 条</template
        ><template v-if="feed.hasMore.value"> · 正在继续加载</template>
      </p>
      <AuditEventTable :events="feed.events.value" />
    </section>
  </section>
</template>
