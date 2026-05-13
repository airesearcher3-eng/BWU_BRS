<template>
  <div class="approval-chain">
    <div class="approval-steps">
      <div :class="['step', isSubmitted ? 'done' : 'pending']">
        <span class="step-num">1</span>
        <span>Submit for Review</span>
      </div>
      <div :class="['step', isApproved ? 'done' : 'pending']">
        <span class="step-num">2</span>
        <span>Manager Approval</span>
      </div>
      <div :class="['step', isSigned ? 'done' : 'pending']">
        <span class="step-num">3</span>
        <span>Controller Sign-off</span>
      </div>
    </div>

    <div class="approval-actions mt-2">
      <button v-if="currentStatus === 'completed'" class="btn btn-sm btn-primary"
              @click="action('submit')">Submit for Review</button>
      <button v-if="currentStatus === 'pending_review'" class="btn btn-sm btn-success"
              @click="action('approve')">Approve</button>
      <button v-if="currentStatus === 'approved'" class="btn btn-sm btn-success"
              @click="action('signoff')">Sign Off</button>
    </div>
    <p v-if="msg" class="success-msg">{{ msg }}</p>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import api from '@/services/api'

const props = defineProps({ runId: Number, currentStatus: String })
const emit = defineEmits(['updated'])
const msg = ref('')

const isSubmitted = computed(() => ['pending_review', 'approved', 'signed_off'].includes(props.currentStatus))
const isApproved = computed(() => ['approved', 'signed_off'].includes(props.currentStatus))
const isSigned = computed(() => props.currentStatus === 'signed_off')

async function action(type) {
  await api.post(`/approval/${props.runId}/${type}`, { comments: '' })
  msg.value = `Action '${type}' completed.`
  emit('updated')
}
</script>
