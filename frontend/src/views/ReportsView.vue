<template>
  <div class="view-container">
    <h2 class="page-title">Reports</h2>
    <div v-if="store.loading" class="loading-text">Loading runs…</div>
    <div v-else class="reports-grid">
      <div v-for="run in store.runs" :key="run.id" class="report-card">
        <div class="report-header">
          <span>Run #{{ run.id }}</span>
          <span :class="`badge badge-${run.status}`">{{ run.status }}</span>
        </div>
        <p>Period: {{ run.period_start }} → {{ run.period_end }}</p>
        <p>Matched: {{ run.total_matched }} / {{ run.total_bank_stmt_entries }}</p>
        <p v-if="run.total_pending > 0" class="warning">Exceptions: {{ run.total_pending }}</p>
        <div class="btn-group mt-2">
          <button class="btn btn-sm btn-primary" @click="store.downloadBRS(run.id)">BRS</button>
          <button class="btn btn-sm btn-secondary" @click="store.downloadMatches(run.id)">Matches</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useReconciliationStore } from '@/stores/reconciliation'

const store = useReconciliationStore()
onMounted(() => store.fetchRuns())
</script>
