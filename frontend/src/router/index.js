import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  { path: '/login', name: 'Login', component: () => import('@/views/LoginView.vue'), meta: { guest: true } },
  { path: '/', name: 'Dashboard', component: () => import('@/views/DashboardView.vue'), meta: { requiresAuth: true } },
  { path: '/reconciliation', name: 'Reconciliation', component: () => import('@/views/ReconciliationView.vue'), meta: { requiresAuth: true } },
  { path: '/exceptions', name: 'Exceptions', component: () => import('@/views/ExceptionsView.vue'), meta: { requiresAuth: true } },
  { path: '/reports', name: 'Reports', component: () => import('@/views/ReportsView.vue'), meta: { requiresAuth: true } },
  { path: '/approvals', name: 'Approvals', component: () => import('@/views/ApprovalsView.vue'), meta: { requiresAuth: true } },
  { path: '/audit', name: 'Audit', component: () => import('@/views/AuditView.vue'), meta: { requiresAuth: true, roles: ['system_admin', 'accounts_manager', 'internal_auditor'] } },
  { path: '/settings', name: 'Settings', component: () => import('@/views/SettingsView.vue'), meta: { requiresAuth: true } },
  { path: '/admin', name: 'Admin', component: () => import('@/views/AdminView.vue'), meta: { requiresAuth: true, roles: ['system_admin'] } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, _from, next) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.isLoggedIn) return next('/login')
  if (to.meta.guest && auth.isLoggedIn) return next('/')
  if (to.meta.roles && !to.meta.roles.includes(auth.user?.role)) return next('/')
  next()
})

export default router
