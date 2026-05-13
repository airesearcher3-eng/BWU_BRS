<template>
  <div class="view-container">
    <h2 class="page-title">Admin Panel</h2>

    <div class="tabs">
      <button :class="{ active: tab === 'users' }" @click="tab = 'users'">Users</button>
      <button :class="{ active: tab === 'bank' }" @click="tab = 'bank'">Bank Accounts</button>
      <button :class="{ active: tab === 'danger' }" @click="tab = 'danger'">Danger Zone</button>
    </div>

    <!-- Users Tab -->
    <div v-if="tab === 'users'" class="card mt-3">
      <div class="card-header">
        <h3>User Management</h3>
        <button class="btn btn-sm btn-primary" @click="showCreateUser = true">+ New User</button>
      </div>
      <table class="data-table">
        <thead><tr><th>Username</th><th>Full Name</th><th>Role</th><th>Active</th><th>Actions</th></tr></thead>
        <tbody>
          <tr v-for="u in adminStore.users" :key="u.id">
            <td>{{ u.username }}</td>
            <td>{{ u.full_name }}</td>
            <td>{{ u.role }}</td>
            <td>{{ u.is_active ? '✅' : '❌' }}</td>
            <td>
              <button class="btn btn-xs btn-danger" @click="adminStore.deactivateUser(u.id)">Deactivate</button>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="showCreateUser" class="modal-overlay" @click.self="showCreateUser = false">
        <div class="modal">
          <h3>Create User</h3>
          <form @submit.prevent="createUser">
            <div class="form-group"><label>Username</label><input v-model="newUser.username" required /></div>
            <div class="form-group"><label>Full Name</label><input v-model="newUser.full_name" required /></div>
            <div class="form-group">
              <label>Role</label>
              <select v-model="newUser.role">
                <option v-for="r in roles" :key="r" :value="r">{{ r }}</option>
              </select>
            </div>
            <div class="form-group"><label>Password</label><input v-model="newUser.password" type="password" minlength="12" required /></div>
            <div class="form-group"><label>Email (optional)</label><input v-model="newUser.email" type="email" /></div>
            <p v-if="createError" class="error-msg">{{ createError }}</p>
            <div class="btn-group">
              <button type="submit" class="btn btn-primary">Create</button>
              <button type="button" class="btn btn-secondary" @click="showCreateUser = false">Cancel</button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- Bank Accounts Tab -->
    <div v-if="tab === 'bank'" class="card mt-3">
      <div class="card-header">
        <h3>Bank Accounts</h3>
      </div>
      <table class="data-table">
        <thead><tr><th>Account No</th><th>Bank</th><th>Branch</th><th>Label</th><th>Active</th><th></th></tr></thead>
        <tbody>
          <tr v-for="ba in adminStore.bankAccounts" :key="ba.id">
            <td>{{ ba.account_no }}</td>
            <td>{{ ba.bank_name }}</td>
            <td>{{ ba.branch }}</td>
            <td>{{ ba.label }}</td>
            <td>{{ ba.is_active ? '✅' : '❌' }}</td>
            <td><button class="btn btn-xs btn-danger" @click="adminStore.deleteBankAccount(ba.id)">Delete</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Danger Zone -->
    <div v-if="tab === 'danger'" class="card mt-3 danger-card">
      <h3>⚠️ Danger Zone</h3>
      <p>This will permanently delete all reconciliation runs, transactions, matches, and exceptions.</p>
      <button class="btn btn-danger" @click="clearDB">Clear All Reconciliation Data</button>
      <p v-if="clearMsg" class="success-msg mt-2">{{ clearMsg }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useAdminStore } from '@/stores/admin'

const adminStore = useAdminStore()
const tab = ref('users')
const showCreateUser = ref(false)
const createError = ref('')
const clearMsg = ref('')

const roles = ['accounts_officer', 'accounts_manager', 'finance_controller', 'internal_auditor', 'system_admin']
const newUser = ref({ username: '', full_name: '', role: 'accounts_officer', password: '', email: '' })

onMounted(() => { adminStore.fetchUsers(); adminStore.fetchBankAccounts() })

async function createUser() {
  createError.value = ''
  try {
    await adminStore.createUser(newUser.value)
    showCreateUser.value = false
    newUser.value = { username: '', full_name: '', role: 'accounts_officer', password: '', email: '' }
  } catch (e) {
    createError.value = e.response?.data?.detail || 'Failed to create user'
  }
}

async function clearDB() {
  if (!confirm('Are you sure? This cannot be undone.')) return
  await adminStore.clearDatabase()
  clearMsg.value = 'Database cleared successfully.'
}
</script>
