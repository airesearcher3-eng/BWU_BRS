import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '@/services/api'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('brs_token') || null)
  const user = ref(null)

  const isLoggedIn = computed(() => !!token.value)

  async function fetchUser() {
    if (!token.value) return
    const { data } = await api.get('/auth/me')
    user.value = data
  }

  async function login(username, password) {
    const form = new URLSearchParams()
    form.append('username', username)
    form.append('password', password)
    const { data } = await api.post('/auth/login', form, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
    token.value = data.access_token
    localStorage.setItem('brs_token', data.access_token)
    await fetchUser()
  }

  function logout() {
    token.value = null
    user.value = null
    localStorage.removeItem('brs_token')
  }

  // Restore user on page load
  if (token.value) fetchUser().catch(logout)

  return { token, user, isLoggedIn, login, logout, fetchUser }
})
