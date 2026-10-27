<script setup lang="ts">
import { RouterLink } from 'vue-router'
import type { TaskSummary } from '@/types/api'
import { formatTime } from '@/utils/audit'
import PhaseBadge from './PhaseBadge.vue'
defineProps<{ tasks: TaskSummary[] }>()
</script>
<template>
  <p v-if="!tasks.length" class="empty-message">
    当前实例暂无任务。通过终端展示入口创建任务后，这里会自动更新。
  </p>
  <div v-else class="table-scroll">
    <table>
      <thead>
        <tr>
          <th>任务</th>
          <th>工作区</th>
          <th>阶段</th>
          <th>创建时间</th>
          <th>更新时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="task in tasks" :key="task.task_id">
          <td>
            <RouterLink class="text-link mono" :to="`/tasks/${encodeURIComponent(task.task_id)}`">{{
              task.task_id
            }}</RouterLink>
          </td>
          <td>{{ task.workspace_id }}</td>
          <td><PhaseBadge :phase="task.phase" /></td>
          <td>{{ formatTime(task.created_at) }}</td>
          <td>{{ formatTime(task.updated_at) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
