<template>
  <div class="file-uploader">
    <label>{{ label }}<span v-if="optional"> (optional)</span></label>
    <div class="upload-area" :class="{ uploaded: filePath, dragging }"
         @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false"
         @drop.prevent="onDrop">
      <input type="file" accept=".xlsx,.xls,.csv" @change="onSelect" ref="inputRef" hidden />
      <div v-if="!filePath" class="upload-prompt" @click="inputRef?.click()">
        <span>📂 Click or drag file here</span>
        <small>XLSX, XLS, CSV</small>
      </div>
      <div v-else class="upload-done">
        ✅ {{ fileName }}
        <button class="btn-clear" @click.stop="clear">✕</button>
      </div>
    </div>
    <p v-if="error" class="error-msg">{{ error }}</p>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import api from '@/services/api'

const props = defineProps({
  label: String,
  optional: Boolean,
  uploadType: { type: String, default: 'bank-statement' },
})
const emit = defineEmits(['uploaded'])

const inputRef = ref(null)
const filePath = ref('')
const fileName = ref('')
const dragging = ref(false)
const error = ref('')

async function upload(file) {
  error.value = ''
  const form = new FormData()
  form.append('file', file)
  try {
    const { data } = await api.post(`/upload/${props.uploadType}`, form)
    filePath.value = data.path
    fileName.value = data.filename
    emit('uploaded', data.path)
  } catch (e) {
    error.value = e.response?.data?.detail || 'Upload failed'
  }
}

function onSelect(e) {
  const f = e.target.files[0]
  if (f) upload(f)
}

function onDrop(e) {
  dragging.value = false
  const f = e.dataTransfer.files[0]
  if (f) upload(f)
}

function clear() {
  filePath.value = ''
  fileName.value = ''
  if (inputRef.value) inputRef.value.value = ''
  emit('uploaded', '')
}
</script>
