<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import MenuPanel from './components/MenuPanel.vue'
import DemoGuide from './components/DemoGuide.vue'
import { demoCases } from './demoCases'
import ClarificationCard from './components/ClarificationCard.vue'
import ConstraintSummary from './components/ConstraintSummary.vue'
import { useChat } from './composables/useChat'
import { getHealth, getProfiles } from './services/api'
import type { DemoProfile, Health } from './types'

const { userId, messages, result, busy, error, reset, send, retry } = useChat()
const profiles = ref<DemoProfile[]>([])
const health = ref<Health | null>(null)
const demoMode = window.location.pathname.replace(/\/$/, '') === '/demo'
const selectedCase = ref<number | null>(null)
const demoCompleted = ref(false)
const conversationKey = ref(0)
const loading = ref(true)
const loadError = ref(false)
const currentProfile = computed(() => profiles.value.find(profile => profile.user_id === userId.value))
const profileNames: Record<number, string> = { 900001: '日常用餐', 900002: '过敏与控糖偏好', 900003: '清淡饮食偏好' }
const ready = computed(() => !!profiles.value.length && health.value?.status === 'ok')
async function connect() {
  loading.value = true; loadError.value = false
  try { const response = await Promise.all([getProfiles(), getHealth()]); profiles.value = response[0]; health.value = response[1]; if (!profiles.value.some(p => p.user_id === userId.value)) reset(profiles.value[0]!.user_id) }
  catch { loadError.value = true }
  finally { loading.value = false }
}
function newConversation(nextUserId = userId.value) { if (reset(nextUserId)) { conversationKey.value += 1; selectedCase.value = null; demoCompleted.value = false } }
function changeProfile(event: Event) { newConversation(Number((event.target as HTMLSelectElement).value)) }
async function startCase(id: number) {
  const item = demoCases.find(entry => entry.id === id)
  if (!item || !ready.value || !reset(900001)) return
  conversationKey.value += 1; selectedCase.value = id; demoCompleted.value = false
  await send(item.initial)
}
async function nextDemoStep(text: string) {
  if (await send(text)) demoCompleted.value = result.value?.status === 'ok'
}
onMounted(connect)
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="/" aria-label="方太膳食伙伴首页"><svg viewBox="0 0 42 42" aria-hidden="true"><rect width="42" height="42" rx="13" fill="currentColor"/><path d="M12 27C12 15 21 11 32 10c-1 13-6 20-15 20" fill="#d7e6b7"/><path d="m12 32 15-17" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg><span>方太<span class="brand-en">膳食伙伴</span></span></a>
      <p class="sidebar-caption">PERSONAL MEAL COMPANION</p>
      <nav class="main-nav" aria-label="主要导航"><a href="/" :class="{active: !demoMode}"><span aria-hidden="true">◈</span> 我的膳食计划</a><a href="/demo" :class="{active: demoMode}"><span aria-hidden="true">▷</span> 三分钟体验</a></nav>
      <section class="profile-panel"><span class="eyebrow">演示画像</span><label for="profile-select">选择用餐者</label><select id="profile-select" :value="userId" data-testid="profile-select" :disabled="busy || !ready" @change="changeProfile"><option v-for="profile in profiles" :key="profile.user_id" :value="profile.user_id">{{ profileNames[profile.user_id] || profile.label }}</option></select><span class="synthetic-pill">合成资料</span><div v-if="currentProfile" class="profile-facts"><p><span>忌口</span>{{ currentProfile.allergies.join('、') || '请在对话中确认' }}</p><p><span>目标</span>{{ currentProfile.health_goals.join('、') || '按本餐需求沟通' }}</p></div></section>
      <button class="new-session" data-testid="new-session" :disabled="busy" @click="newConversation()"><span aria-hidden="true">＋</span> 开始新对话</button>
      <div class="sidebar-bottom"><span class="small-leaf" aria-hidden="true">❋</span><p>每一餐，都有依据。<br><span>每一个选择，都更懂你。</span></p><small>ZX-2026-0301 · Demo v0.3.0</small></div>
    </aside>
    <main class="main-content">
      <header class="topbar"><span>你的饮食，你来定义</span><div class="service-status"><span :class="['status-dot', { connected: ready }]"></span>{{ loading ? '正在连接服务' : ready ? '膳食伙伴已就绪' : '服务暂未连接' }}<a href="/docs" target="_blank" rel="noopener">API ↗</a></div></header>
      <section class="welcome-banner"><div><span class="eyebrow">EAT WELL. FEEL GOOD.</span><h1>好好吃饭，<em>从了解你开始。</em></h1><p>聊聊你的需求，让这一餐有滋味，也有依据。</p></div><div class="banner-art" aria-hidden="true"><div class="banner-plate"><span class="leaf one"></span><span class="leaf two"></span><span class="leaf three"></span><span class="seed"></span></div><span class="orbit-dot"></span></div></section>
      <div v-if="loadError" class="connection-error" role="alert">暂时无法连接膳食服务，请确认服务已启动。<button @click="connect">重新连接</button></div>
      <DemoGuide v-if="demoMode" :busy="busy" :ready="ready" :selected-case="selectedCase" :result="result" :completed="demoCompleted" @start="startCase" @next="nextDemoStep" />
      <ConstraintSummary :result="result" :busy="busy" />
      <div class="workspace">
        <ChatPanel :key="conversationKey" :messages="messages" :result="result" :busy="busy" :error="error" :ready="ready" @send="send" @retry="retry" @reset="newConversation()"><template #clarification><ClarificationCard v-if="result?.status === 'clarification_required'" :questions="result.clarification_questions" :busy="busy" @answer="send" /></template></ChatPanel>
        <div class="result-column"><MenuPanel :result="result" :busy="busy || !!error" @replace="slot => send(`只替换第${slot}道菜，其他保持不变。`)" @explain="send('解释这份菜单，不要调整菜品。')" /><details v-if="result?.warnings.length" class="result-warnings"><summary>本餐提示与数据边界 · {{ result.warnings.length }} 项</summary><p v-for="(warning,index) in result.warnings" :key="index">{{ warning }}</p></details></div>
      </div>
      <footer class="page-footer"><span>方太个性化膳食规划 Agent</span><span>真实菜谱 · 合成画像 · 营养解释有依据</span></footer>
    </main>
  </div>
</template>
