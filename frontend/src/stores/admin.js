import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/services/api'

export const useAdminStore = defineStore('admin', () => {
  const users = ref([])
  const bankAccounts = ref([])

  async function fetchUsers() {
    const { data } = await api.get('/admin/users')
    users.value = data
  }

  async function createUser(payload) {
    await api.post('/admin/users', payload)
    await fetchUsers()
  }

  async function updateUser(id, payload) {
    await api.put(`/admin/users/${id}`, payload)
    await fetchUsers()
  }

  async function deactivateUser(id) {
    await api.delete(`/admin/users/${id}`)
    await fetchUsers()
  }

  async function resetPassword(id, new_password) {
    await api.post(`/admin/users/${id}/reset-password`, { new_password })
  }

  async function fetchBankAccounts() {
    const { data } = await api.get('/admin/bank-accounts')
    bankAccounts.value = data
  }

  async function createBankAccount(payload) {
    await api.post('/admin/bank-accounts', payload)
    await fetchBankAccounts()
  }

  async function deleteBankAccount(id) {
    await api.delete(`/admin/bank-accounts/${id}`)
    await fetchBankAccounts()
  }

  async function clearDatabase() {
    await api.post('/admin/clear-database')
  }

  return {
    users, bankAccounts,
    fetchUsers, createUser, updateUser, deactivateUser, resetPassword,
    fetchBankAccounts, createBankAccount, deleteBankAccount, clearDatabase,
  }
})
