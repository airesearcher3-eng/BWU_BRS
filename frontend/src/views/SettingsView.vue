<template>
  <div class="view-container">
    <h2 class="page-title">Settings</h2>
    <div class="card">
      <h3>Change Password</h3>
      <form @submit.prevent="changePassword">
        <div class="form-group">
          <label>Current Password</label>
          <input v-model="current" type="password" required />
        </div>
        <div class="form-group">
          <label>New Password</label>
          <input v-model="newPass" type="password" minlength="6" required />
        </div>
        <div class="form-group">
          <label>Confirm New Password</label>
          <input v-model="confirm" type="password" required />
        </div>
        <p v-if="msg" :class="msgClass">{{ msg }}</p>
        <button type="submit" class="btn btn-primary" :disabled="saving">
          {{ saving ? 'Saving…' : 'Update Password' }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import api from '@/services/api'

const current = ref('')
const newPass = ref('')
const confirm = ref('')
const saving = ref(false)
const msg = ref('')
const msgClass = ref('')

async function changePassword() {
  if (newPass.value !== confirm.value) { msg.value = 'Passwords do not match'; msgClass.value = 'error-msg'; return }
  saving.value = true
  msg.value = ''
  try {
    await api.post('/auth/change-password', { current_password: current.value, new_password: newPass.value })
    msg.value = 'Password updated successfully'
    msgClass.value = 'success-msg'
    current.value = newPass.value = confirm.value = ''
  } catch (e) {
    msg.value = e.response?.data?.detail || 'Failed to update password'
    msgClass.value = 'error-msg'
  } finally {
    saving.value = false
  }
}
</script>
