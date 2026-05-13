<template>
  <div class="view-container">
    <h2 class="page-title">Exceptions</h2>

    <div class="filter-bar">
      <select v-model="filterStatus" @change="load">
        <option value="">All Statuses</option>
        <option value="open">Open</option>
        <option value="escalated">Escalated</option>
        <option value="resolved">Resolved</option>
      </select>
    </div>

    <div v-if="store.loading" class="loading-text">Loading…</div>
    <div v-else-if="!store.list.length" class="empty-state">No exceptions found.</div>
    <div v-else class="exceptions-list">
      <div v-for="exc in store.list" :key="exc.id" class="exception-card"
           :class="`exc-${exc.status}`" @click="openModal(exc.id)">
        <div class="exc-header">
          <span class="exc-type">{{ exc.exception_type }}</span>
          <span :class="`badge badge-${exc.status}`">{{ exc.status }}</span>
        </div>
        <p class="exc-section">{{ exc.brs_section }}</p>
        <p class="exc-sla">SLA: {{ exc.sla_days }} days</p>
      </div>
    </div>

    <ExceptionModal v-if="modalId" :exc-id="modalId"
                    @close="modalId = null" @resolved="load" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useExceptionsStore } from '@/stores/exceptions'
import ExceptionModal from '@/components/ExceptionModal.vue'

const store = useExceptionsStore()
const filterStatus = ref('')
const modalId = ref(null)

onMounted(() => load())

function load() {
  store.fetchExceptions(null, filterStatus.value || null)
}

function openModal(id) {
  modalId.value = id
}
</script>
