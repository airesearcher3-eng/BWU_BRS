import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/services/api'

export const useExceptionsStore = defineStore('exceptions', () => {
  const list = ref([])
  const selected = ref(null)
  const loading = ref(false)

  async function fetchExceptions(runId = null, status = null) {
    loading.value = true
    const params = {}
    if (runId) params.run_id = runId
    if (status) params.status = status
    const { data } = await api.get('/exceptions', { params })
    list.value = data
    loading.value = false
  }

  async function fetchException(excId) {
    const { data } = await api.get(`/exceptions/${excId}`)
    selected.value = data
    return data
  }

  async function resolve(excId, resolution_type = 'manual_match') {
    await api.post(`/exceptions/${excId}/resolve`, { resolution_type })
    await fetchExceptions()
  }

  async function escalate(excId) {
    await api.post(`/exceptions/${excId}/escalate`)
    await fetchExceptions()
  }

  async function addComment(excId, comment) {
    await api.post(`/exceptions/${excId}/comment`, { comment })
    if (selected.value?.id === excId) await fetchException(excId)
  }

  return { list, selected, loading, fetchExceptions, fetchException, resolve, escalate, addComment }
})
