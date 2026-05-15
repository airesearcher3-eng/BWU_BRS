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

  // Used to cancel an in-progress poll loop from outside startRun()
  let _cancelPolling = null

  async function startRun(payload) {
    loading.value = true
    error.value = null
    try {
      // POST returns immediately with { run_id, status: "running" }
      const { data: started } = await api.post('/reconciliation/run', payload)
      const runId = started.run_id

      // Poll GET /run/{id} every 3 s until completed, failed, or cancelled.
      // No hard timeout — the user can cancel via cancelPolling().
      const result = await new Promise((resolve, reject) => {
        const INTERVAL = 3000
        const timer = setInterval(async () => {
          try {
            const { data: run } = await api.get(`/reconciliation/run/${runId}`)
            if (run.status === 'completed') {
              clearInterval(timer)
              _cancelPolling = null
              currentRun.value = run
              resolve(run)
            } else if (run.status === 'failed') {
              clearInterval(timer)
              _cancelPolling = null
              reject(new Error(`Run #${runId} failed. Check audit logs for details.`))
            }
            // still "running" — keep polling
          } catch (pollErr) {
            clearInterval(timer)
            _cancelPolling = null
            reject(pollErr)
          }
        }, INTERVAL)

        // Expose cancellation: caller sets _cancelPolling, we wire it here
        _cancelPolling = () => {
          clearInterval(timer)
          _cancelPolling = null
          resolve(null) // null signals a user cancel (not an error)
        }
      })

      if (result === null) return null  // cancelled by user
      await fetchRuns()
      return currentRun.value
    } catch (e) {
      error.value = e.response?.data?.detail || e.message
      throw e
    } finally {
      loading.value = false
    }
  }

  function cancelPolling() {
    if (_cancelPolling) {
      _cancelPolling()
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

  return { runs, currentRun, loading, error, fetchRuns, fetchRun, startRun, cancelPolling, downloadBRS, downloadMatches, deleteRun }
})
