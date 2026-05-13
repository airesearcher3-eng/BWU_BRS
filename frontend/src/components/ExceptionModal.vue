<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal">
      <div v-if="loading" class="loading-text">Loading…</div>
      <div v-else-if="exc">
        <div class="modal-header">
          <h3>Exception #{{ exc.id }} — {{ exc.exception_type }}</h3>
          <button class="btn-close" @click="$emit('close')">✕</button>
        </div>
        <dl class="exc-details">
          <dt>Section</dt><dd>{{ exc.brs_section }}</dd>
          <dt>Date</dt><dd>{{ exc.transaction_date }}</dd>
          <dt>Amount</dt><dd>₹{{ exc.amount }}</dd>
          <dt>Direction</dt><dd>{{ exc.direction }}</dd>
          <dt>Description</dt><dd>{{ exc.description || exc.narration }}</dd>
          <dt>Status</dt><dd><span :class="`badge badge-${exc.status}`">{{ exc.status }}</span></dd>
          <dt>SLA</dt><dd>{{ exc.sla_days }} days</dd>
        </dl>

        <div class="suggestion-box">
          <strong>Suggested Action:</strong>
          <p>{{ exc.suggested_solution }}</p>
        </div>

        <div class="comments-section">
          <h4>Comments</h4>
          <div v-for="c in exc.comments" :key="c.id" class="comment">
            <span class="commenter">{{ c.commenter_name || 'System' }}</span>
            <span class="comment-text">{{ c.comment_text }}</span>
            <span class="comment-time">{{ c.created_at }}</span>
          </div>
          <div class="add-comment">
            <textarea v-model="comment" placeholder="Add a comment…" rows="2"></textarea>
            <button class="btn btn-sm btn-secondary" @click="addComment">Add</button>
          </div>
        </div>

        <div class="btn-group mt-3">
          <button v-if="exc.status === 'open'" class="btn btn-success" @click="resolve">Mark Resolved</button>
          <button v-if="exc.status === 'open'" class="btn btn-warning" @click="escalate">Escalate</button>
          <button class="btn btn-secondary" @click="$emit('close')">Close</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useExceptionsStore } from '@/stores/exceptions'

const props = defineProps({ excId: { type: Number, required: true } })
const emit = defineEmits(['close', 'resolved'])
const store = useExceptionsStore()
const exc = ref(null)
const loading = ref(false)
const comment = ref('')

watch(() => props.excId, async id => {
  if (!id) return
  loading.value = true
  exc.value = await store.fetchException(id)
  loading.value = false
}, { immediate: true })

async function resolve() {
  await store.resolve(props.excId)
  emit('resolved')
  emit('close')
}

async function escalate() {
  await store.escalate(props.excId)
  emit('resolved')
  emit('close')
}

async function addComment() {
  if (!comment.value.trim()) return
  await store.addComment(props.excId, comment.value)
  comment.value = ''
  exc.value = await store.fetchException(props.excId)
}
</script>
