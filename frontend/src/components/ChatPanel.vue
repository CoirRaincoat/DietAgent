<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { ChatResult, Message } from '../types'
import type { ApiError } from '../services/api'
const props = defineProps<{ messages: Message[]; result: ChatResult | null; busy: boolean; error: ApiError | null; ready: boolean }>()
const emit = defineEmits<{ send: [text: string]; retry: []; reset: [] }>()
const input = ref('')
const scrollArea = ref<HTMLElement>()
function submit() {
  if (!input.value.trim() || props.busy || !props.ready || input.value.length > 2000 || props.error?.retryable || props.error?.conflict) return
  emit('send', input.value)
  input.value = ''
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); submit() }
}
watch(() => [props.messages.length, props.busy], async () => { await nextTick(); scrollArea.value?.scrollTo({ top: scrollArea.value.scrollHeight, behavior: 'smooth' }) })
</script>

<template>
  <section class="chat-panel" aria-label="膳食对话">
    <header class="panel-heading"><div class="assistant-mark" aria-hidden="true">✳</div><div><h2>和膳食伙伴聊聊</h2><p>把你的想法，变成这一餐</p></div><span class="live-dot" :class="{ offline: !ready }" :title="ready ? '服务已连接' : '等待服务连接'"></span></header>
    <div ref="scrollArea" class="chat-scroll" role="log" aria-live="polite" aria-relevant="additions text">
      <div v-if="!messages.length" class="chat-welcome">
        <span class="eyebrow">从一句话开始</span>
        <h3>今天，想怎样好好吃饭？</h3>
        <p>告诉我你的口味和目标。我会先确认用餐信息，再从菜谱库里为你安排。</p>
        <div class="starter-list">
          <button :disabled="!ready || busy" @click="emit('send', '最近想减脂，帮我安排晚餐')"><span>🥗</span> 最近想减脂，晚餐怎么吃？<span aria-hidden="true">↗</span></button>
          <button :disabled="!ready || busy" @click="emit('send', '2人晚餐，没有其他忌口，不吃辣，安排三道菜。')"><span>🍲</span> 两个人，不吃辣，安排晚餐<span aria-hidden="true">↗</span></button>
        </div>
        <p class="synthetic-note">当前使用合成演示资料，请用虚构需求体验。</p>
      </div>
      <article v-for="message in messages" :key="message.id" :class="['chat-message', message.role, { failed: message.failed }]">
        <div class="message-label">{{ message.role === 'user' ? '你' : '膳食伙伴' }}<span v-if="message.failed"> · 暂未完成</span></div>
        <p class="message-text">{{ message.text }}</p>
        <span v-if="message.response?.status === 'no_feasible_menu'" class="state-badge caution">当前条件下暂无可行菜单</span>
        <slot v-if="message.response && message === messages[messages.length - 1]" name="clarification" />
      </article>
      <div v-if="busy" class="thinking" role="status"><span class="thinking-dot"></span>正在理解并核对你的需求…</div>
    </div>
    <div v-if="error" class="chat-error" role="alert" data-testid="chat-error">
      <p>{{ error.message }}</p>
      <button v-if="error.retryable" data-testid="retry-request" :disabled="busy" @click="emit('retry')">重试这次请求</button>
      <button v-if="error.conflict || error.retryable" :disabled="busy" @click="emit('reset')">开始新对话</button>
    </div>
    <form class="composer" @submit.prevent="submit">
      <label for="chat-input" class="sr-only">输入用餐需求</label>
      <textarea id="chat-input" v-model="input" data-testid="chat-input" rows="2" maxlength="2000" placeholder="例如：今晚两个人吃，不吃辣…" :disabled="busy || !ready || error?.retryable || error?.conflict" @keydown="onKeydown" />
      <div class="composer-footer"><span>Enter 发送 · Shift + Enter 换行</span><button type="submit" data-testid="send-message" class="send-button" :disabled="busy || !ready || !input.trim() || error?.retryable || error?.conflict" aria-label="发送消息"><span>发送</span><span aria-hidden="true">↑</span></button></div>
    </form>
    <p class="composer-note">基于菜谱资料提供建议，不替代个体营养诊疗。</p>
  </section>
</template>
