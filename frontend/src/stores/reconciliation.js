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

  function _triggerDownload(blob, contentType, filename) {
    const url = URL.createObjectURL(new Blob([blob], { type: contentType }))
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(url), 10000)
  }

  async function downloadBRS(runId) {
    try {
      const { data, headers } = await api.get(`/reconciliation/run/${runId}/download`, { responseType: 'blob' })
      _triggerDownload(data, headers['content-type'], `BRS_run_${runId}.xlsx`)
    } catch (e) {
      alert(`Download failed: ${e.response?.data?.detail || e.message}`)
    }
  }

  async function downloadMatches(runId) {
    try {
      const { data, headers } = await api.get(`/reconciliation/run/${runId}/matches/download`, { responseType: 'blob' })
      _triggerDownload(data, headers['content-type'], `Matched_Report_Run_${runId}.xlsx`)
    } catch (e) {
      alert(`Download failed: ${e.response?.data?.detail || e.message}`)
    }
  }

  async function deleteRun(runId) {
    if (!confirm(`Delete Run #${runId}? This cannot be undone.`)) return
    await api.delete(`/reconciliation/run/${runId}`)
    runs.value = runs.value.filter(r => r.id !== runId)
  }

  return { runs, currentRun, loading, error, fetchRuns, fetchRun, startRun, downloadBRS, downloadMatches, deleteRun }
})
