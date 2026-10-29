<script setup lang="ts">
import { getHealth } from '@/api'
import { useBrokerSession } from '@/composables/useBrokerSession'
import { usePolling } from '@/composables/usePolling'
import RequestState from '@/components/RequestState.vue'

const session = useBrokerSession()
const health = usePolling((context) => getHealth(context.signal))
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
    <p class="instance-label">当前实例</p>
    <p class="instance-id mono">{{ session.instanceId.value || '等待查询' }}</p>
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
  border-top: 1px solid #e1e6df;
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
}
.instance-label {
  margin: 18px 0 5px;
  color: #84938c;
  font-size: 11px;
}
.instance-id {
  margin: 0;
  color: #536b5e;
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
}
@media (max-width: 760px) {
  .service-connection { margin-top: 0; }
}
</style>
