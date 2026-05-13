<template>
  <div class="view-container">
    <h2 class="page-title">Audit Log</h2>
    <div class="filter-bar">
      <input v-model="search" placeholder="Filter by action…" @input="load" />
    </div>
    <div v-if="loading" class="loading-text">Loading…</div>
    <table v-else class="data-table">
      <thead><tr><th>Timestamp</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead>
      <tbody>
        <tr v-for="row in logs" :key="row.id">
          <td>{{ row.timestamp }}</td>
          <td>{{ row.action }}</td>
          <td>{{ row.entity_type }} #{{ row.entity_id }}</td>
          <td><code>{{ JSON.stringify(row.details) }}</code></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '@/services/api'

const logs = ref([])
const loading = ref(false)
const search = ref('')

onMounted(() => load())

async function load() {
  loading.value = true
  const params = { limit: 200 }
  if (search.value) params.action = search.value
  const { data } = await api.get('/audit', { params })
  logs.value = data
  loading.value = false
}
</script>
