import { ref } from 'vue'
import { describeError, postChat, type ApiError } from '../services/api'
import type { ChatRequest, ChatResult, Message } from '../types'

export function useChat() {
  const userId = ref(900001)
  const sessionId = ref<string>()
  const messages = ref<Message[]>([])
  const result = ref<ChatResult | null>(null)
  const busy = ref(false)
  const error = ref<ApiError | null>(null)
  let pending: ChatRequest | null = null
  let failedMessageId: string | null = null

  function reset(nextUserId = userId.value) {
    if (busy.value) return false
    userId.value = nextUserId
    sessionId.value = undefined
    messages.value = []
    result.value = null
    error.value = null
    pending = null
    failedMessageId = null
    return true
  }
  async function execute(payload: ChatRequest) {
    busy.value = true
    error.value = null
    try {
      const response = await postChat(payload)
      sessionId.value = response.conversation_state.session_id
      result.value = response
      messages.value = messages.value.map(message => message.id === failedMessageId ? { ...message, failed: false } : message)
      messages.value.push({ id: crypto.randomUUID(), role: 'assistant', text: response.reason, response })
      pending = null
      failedMessageId = null
      return true
    } catch (failure) {
      error.value = describeError(failure)
      // The server may have advanced after a lost response; do not label the old menu current.
      result.value = null
      messages.value = messages.value.map(message => message.id === failedMessageId ? { ...message, failed: true } : message)
      return false
    } finally {
      busy.value = false
    }
  }
  async function send(text: string) {
    const message = text.trim()
    if (busy.value || !message || message.length > 2000 || error.value?.conflict) return false
    // A transport failure might already have applied the request. Resolve it before new work.
    if (pending && error.value?.retryable) return false
    const payload: ChatRequest = { user_id: userId.value, message, request_id: crypto.randomUUID() }
    if (sessionId.value) payload.session_id = sessionId.value
    failedMessageId = crypto.randomUUID()
    messages.value.push({ id: failedMessageId, role: 'user', text: message })
    pending = payload
    return execute(payload)
  }
  async function retry() {
    if (!pending || busy.value || !error.value?.retryable) return false
    return execute(pending)
  }
  return { userId, sessionId, messages, result, busy, error, reset, send, retry }
}
