import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

export const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'welcome',
    component: () => import('@/views/WelcomeView.vue'),
    meta: { title: '欢迎', layout: 'landing' },
  },
  {
    path: '/workspaces',
    name: 'workspaces',
    component: () => import('@/views/WorkspacesView.vue'),
    meta: { title: '工作区管理' },
  },
  { path: '/dashboard', redirect: '/workspaces' },
  {
    path: '/tasks',
    name: 'tasks',
    component: () => import('@/views/TaskListView.vue'),
    meta: { title: '任务列表' },
  },
  {
    path: '/tasks/:taskId',
    name: 'task-detail',
    component: () => import('@/views/TaskDetailView.vue'),
    props: true,
    meta: { title: '任务详情' },
  },
  // Audit events now live inside the task detail page; keep old links working.
  { path: '/audit', redirect: '/tasks' },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  document.title = `${String(to.meta.title ?? '控制台')} · AgentFence Lite`
})

export default router
