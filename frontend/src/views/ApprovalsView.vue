<template>
  <div class="view-container">
    <h2 class="page-title">Approvals</h2>
    <div v-if="!store.runs.length" class="loading-text">Loading…</div>
    <div v-else>
      <div v-for="run in completedRuns" :key="run.id" class="approval-card card mb-3">
        <h4>Run #{{ run.id }} — {{ run.period_start }} to {{ run.period_end }}</h4>
        <p>Status: <strong>{{ run.status }}</strong></p>
        <ApprovalChain :run-id="run.id" :current-status="run.status" @updated="store.fetchRuns()" />
      </div>
      <p v-if="!completedRuns.length" class="empty-state">No completed runs awaiting approval.</p>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useReconciliationStore } from '@/stores/reconciliation'
import ApprovalChain from '@/components/ApprovalChain.vue'

const store = useReconciliationStore()
onMounted(() => store.fetchRuns())

const completedRuns = computed(() =>
  store.runs.filter(r => ['completed', 'pending_review', 'approved'].includes(r.status))
)
</script>
