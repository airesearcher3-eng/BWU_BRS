<template>
  <div class="stats-grid">
    <div v-for="stat in stats" :key="stat.label" class="stat-card">
      <span v-if="stat.icon" class="stat-icon">{{ stat.icon }}</span>
      <div class="stat-value">{{ stat.value }}</div>
      <div class="stat-label">{{ stat.label }}</div>
    </div>
  </div>
</template>

<script setup>
defineProps({ stats: { type: Array, default: () => [] } })
</script>
