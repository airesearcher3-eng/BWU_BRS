import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/services/api'

export const useReconciliationStore = defineStore('reconciliation', () => {
  const runs = ref([])
  const currentRun = ref(null)
  const loading = ref(false)
  const error = ref(null)

  async function fetchRuns() {
    loading.value = true
    error.value = null
    try {
      const { data } = await api.get('/reconciliation/runs')
      runs.value = data
    } catch (e) {
      error.value = e.response?.data?.detail || e.message
    } finally {
      loading.value = false
    }
  }

  async function fetchRun(runId) {
    const { data } = await api.get(`/reconciliation/run/${runId}`)
    currentRun.value = data
    return data
  }

  async function startRun(payload) {
    loading.value = true
    error.value = null
    try {
      const { data } = await api.post('/reconciliation/run', payload)
      await fetchRuns()
      return data
    } catch (e) {
      error.value = e.response?.data?.detail || e.message
      throw e
    } finally {
      loading.value = false
    }
  }

  function downloadBRS(runId) {
    window.open(`/api/reconciliation/run/${runId}/download`, '_blank')
  }

  function downloadMatches(runId) {
    window.open(`/api/reconciliation/run/${runId}/matches/download`, '_blank')
  }

  return { runs, currentRun, loading, error, fetchRuns, fetchRun, startRun, downloadBRS, downloadMatches }
})
