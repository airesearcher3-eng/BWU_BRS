<template>
  <div id="brs-app">
    <AppHeader v-if="authStore.isLoggedIn" />
    <main :class="{ 'with-header': authStore.isLoggedIn }">
      <RouterView />
    </main>
  </div>
</template>

<script setup>
import { useAuthStore } from '@/stores/auth'
import AppHeader from '@/components/AppHeader.vue'

const authStore = useAuthStore()
</script>
