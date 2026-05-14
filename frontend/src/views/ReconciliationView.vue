<template>
  <div class="view-container">
    <h2 class="page-title">New Reconciliation</h2>

    <div class="card">
      <form @submit.prevent="run" class="recon-form">
        <div class="form-row">
          <div class="form-group">
            <label>Period Start</label>
            <input v-model="form.period_start" type="date" />
          </div>
          <div class="form-group">
            <label>Period End</label>
            <input v-model="form.period_end" type="date" />
          </div>
        </div>

        <FileUploader label="Bank Statement (XLSX/CSV)" upload-type="bank-statement" @uploaded="p => form.bank_statement_path = p" />
        <FileUploader label="Bank Book (XLSX)" upload-type="bank-book" @uploaded="p => form.bank_book_path = p" />
        <FileUploader label="Previous BRS (optional)" upload-type="previous-brs" optional @uploaded="p => form.previous_brs_path = p" />

        <p v-if="error" class="error-msg">{{ error }}</p>
        <button type="submit" :disabled="!canRun || store.loading" class="btn btn-primary">
          {{ store.loading ? 'Running…' : 'Start Reconciliation' }}
        </button>
      </form>
    </div>

    <div v-if="result" class="card mt-4 result-card">
      <h3>Run #{{ result.run_id }} Completed</h3>
      <StatsGrid :stats="resultStats" />
      <div class="btn-group mt-3">
        <button class="btn btn-success" @click="store.downloadBRS(result.run_id)">
          ⬇ Download BRS Excel
        </button>
        <button class="btn btn-secondary" @click="store.downloadMatches(result.run_id)">
          ⬇ Download Match Report
        </button>
      </div>
    </div>

    <div class="card mt-4">
      <div class="card-header"><h3>All Runs</h3></div>
      <RunTable :runs="store.runs" @download="store.downloadBRS" @matches="store.downloadMatches" @delete="store.deleteRun" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useReconciliationStore } from '@/stores/reconciliation'
import FileUploader from '@/components/FileUploader.vue'
import StatsGrid from '@/components/StatsGrid.vue'
import RunTable from '@/components/RunTable.vue'

const store = useReconciliationStore()
const form = ref({ period_start: '', period_end: '', bank_statement_path: '', bank_book_path: '', previous_brs_path: '' })
const result = ref(null)
const error = ref('')

onMounted(() => store.fetchRuns())

const canRun = computed(() => form.value.bank_statement_path && form.value.bank_book_path)

const resultStats = computed(() => result.value ? [
  { label: 'Stmt Entries', value: result.value.total_bank_stmt },
  { label: 'Book Entries', value: result.value.total_bank_book },
  { label: 'Matched', value: result.value.total_matched },
  { label: 'Match Rate', value: `${result.value.auto_match_rate}%` },
  { label: 'Exceptions', value: result.value.exception_count },
] : [])

async function run() {
  error.value = ''
  result.value = null
  try {
    result.value = await store.startRun(form.value)
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}
</script>
