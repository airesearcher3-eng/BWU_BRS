<template>
  <div class="view-container">
    <h2 class="page-title">Dashboard</h2>

    <StatsGrid :stats="stats" />

    <div class="card mt-4">
      <div class="card-header">
        <h3>Recent Reconciliation Runs</h3>
        <RouterLink to="/reconciliation" class="btn btn-sm btn-primary">New Run</RouterLink>
      </div>
      <div v-if="store.loading" class="loading-text">Loading…</div>
      <RunTable v-else :runs="store.runs.slice(0, 10)" @download="store.downloadBRS" @matches="store.downloadMatches" @delete="store.deleteRun" />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useReconciliationStore } from '@/stores/reconciliation'
import StatsGrid from '@/components/StatsGrid.vue'
import RunTable from '@/components/RunTable.vue'

const store = useReconciliationStore()

onMounted(() => store.fetchRuns())

const stats = computed(() => {
  const runs = store.runs
  const completed = runs.filter(r => r.status === 'completed').length
  const last = runs[0]
  return [
    { label: 'Total Runs', value: runs.length, icon: '📊' },
    { label: 'Completed', value: completed, icon: '✅' },
    { label: 'Last Match Rate', value: last ? `${last.auto_match_rate ?? '—'}%` : '—', icon: '🎯' },
    { label: 'Pending Exceptions', value: runs.reduce((s, r) => s + (r.total_pending || 0), 0), icon: '⚠️' },
  ]
})
</script>
