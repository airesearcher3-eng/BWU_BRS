<template>
  <header class="app-header">
    <div class="header-brand">
      <RouterLink to="/">BWU BRS</RouterLink>
    </div>
    <nav class="header-nav">
      <RouterLink to="/">Dashboard</RouterLink>
      <RouterLink to="/reconciliation">Reconciliation</RouterLink>
      <RouterLink to="/exceptions">Exceptions</RouterLink>
      <RouterLink to="/reports">Reports</RouterLink>
      <RouterLink to="/approvals">Approvals</RouterLink>
      <RouterLink v-if="canAudit" to="/audit">Audit</RouterLink>
      <RouterLink v-if="isAdmin" to="/admin">Admin</RouterLink>
    </nav>
    <div class="header-user">
      <span>{{ auth.user?.full_name }}</span>
      <RouterLink to="/settings" class="btn btn-xs btn-secondary">Settings</RouterLink>
      <button class="btn btn-xs btn-danger" @click="auth.logout(); $router.push('/login')">Logout</button>
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const isAdmin = computed(() => auth.user?.role === 'system_admin')
const canAudit = computed(() => ['system_admin', 'accounts_manager', 'internal_auditor'].includes(auth.user?.role))
</script>
