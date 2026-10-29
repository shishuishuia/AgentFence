<script setup lang="ts">
import { getHealth } from '@/api'
import { useBrokerSession } from '@/composables/useBrokerSession'
import { usePolling } from '@/composables/usePolling'
import RequestState from '@/components/RequestState.vue'
import { shortId } from '@/utils/audit'

const session = useBrokerSession()
const health = usePolling((context) => getHealth(context.signal), { interval: 60000 })
</script>

<template>
  <section class="service-connection" aria-labelledby="service-connection-title">
    <div class="connection-heading">
      <h2 id="service-connection-title">服务连接</h2>
      <span
        class="status-label"
        :class="health.error.value ? 'failed' : health.data.value ? 'connected' : 'loading'"
        role="status"
        >{{ health.error.value ? '未连接' : health.data.value ? '已连接' : '检测中' }}</span
      >
    </div>
    <p class="instance-label">当前服务实例</p>
    <p class="instance-id mono" :title="session.instanceId.value || ''">
      {{ shortId(session.instanceId.value) }}
    </p>
    <p class="instance-hint">服务重启后实例会变化，审计事件按实例隔离。</p>
    <RequestState
      :loading="health.loading.value"
      :error="health.error.value"
      :updated-at="health.updatedAt.value"
      :has-data="!!health.data.value"
      @refresh="health.refresh"
    />
  </section>
</template>

<style scoped>
.service-connection {
  margin-top: 30px;
  padding: 17px 12px;
  border-top: 1px solid #2a3d31;
  min-width: 0;
}
.connection-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.connection-heading h2 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: #e6efe8;
}
.instance-label {
  margin: 18px 0 5px;
  color: #8ea596;
  font-size: 11px;
}
.instance-id {
  margin: 0;
  color: #c2d6c8;
  font-size: 11px;
  line-height: 1.6;
}
.instance-hint {
  margin: 6px 0 0;
  color: #82998b;
  font-size: 11px;
  line-height: 1.6;
}
.service-connection :deep(.request-state) {
  display: grid;
  justify-items: start;
  gap: 6px;
  margin: 14px 0 0;
  font-size: 11px;
  overflow-wrap: anywhere;
  color: #9db3a5;
}
.status-label {
  background: #24382b;
  color: #a9c3b3;
}
.status-label.connected {
  background: #1f4630;
  color: #8fe0aa;
}
.status-label.failed {
  background: #4a2b25;
  color: #f0b3a2;
}
.service-connection :deep(.button) {
  background: #22382a;
  color: #cfe1d5;
  border-color: #33503e;
}
.service-connection :deep(.button:hover) {
  background: #2b4534;
}
@media (max-width: 760px) {
  .service-connection { margin-top: 0; }
}
</style>
