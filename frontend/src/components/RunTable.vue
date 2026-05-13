<template>
  <div class="table-wrapper">
    <table v-if="runs.length" class="data-table">
      <thead>
        <tr>
          <th>Run #</th><th>Period</th><th>Status</th><th>Matched</th><th>Match %</th><th>Exceptions</th><th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="run in runs" :key="run.id">
          <td>#{{ run.id }}</td>
          <td>{{ run.period_start }} → {{ run.period_end }}</td>
          <td><span :class="`badge badge-${run.status}`">{{ run.status }}</span></td>
          <td>{{ run.total_matched }} / {{ run.total_bank_stmt_entries }}</td>
          <td>{{ run.auto_match_rate != null ? run.auto_match_rate + '%' : '—' }}</td>
          <td>{{ run.total_pending || 0 }}</td>
          <td>
            <button class="btn btn-xs btn-primary" @click="$emit('download', run.id)">⬇ BRS</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty-state">No runs yet.</p>
  </div>
</template>

<script setup>
defineProps({ runs: { type: Array, default: () => [] } })
defineEmits(['download'])
</script>
