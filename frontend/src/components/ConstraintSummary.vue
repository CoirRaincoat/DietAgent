<script setup lang="ts">
import { computed } from 'vue'
import type { ChatResult } from '../types'
const props = defineProps<{ result: ChatResult | null; busy: boolean }>()
const labels: Record<string,string> = { people: '用餐人数', meal_type: '餐次', restrictions: '忌口' }
const tools: Record<string,string> = {recipe_search: '查找菜谱',health_check: '核对限制',menu_modify: '安排菜单',nutrition_analysis: '营养解释'}
const confirmed = computed(() => props.result?.conversation_state.confirmed_fields || [])
const events = computed(() => [...new Set(props.result?.tool_calls.map(event => event.name) || [])])
</script>
<template>
  <section class="constraint-summary" aria-label="Agent 用餐信息状态">
    <div class="context-fields"><span class="context-label">本餐信息</span><span v-for="(label,key) in labels" :key="key" :class="['context-chip',{confirmed: confirmed.includes(key)}]"><span aria-hidden="true">{{ confirmed.includes(key) ? '✓' : '○' }}</span> {{ label }}</span><span v-if="busy" class="updating" role="status">正在更新…</span></div>
    <details v-if="result?.constraints.length || events.length" class="context-details"><summary>已记录的偏好与执行记录</summary><div class="constraint-tags"><span v-for="text in result?.constraints" :key="text">{{ text }}</span></div><p v-if="events.length" class="executed">本轮已执行：{{ events.map(name => tools[name] || name).join(' → ') }}</p><p v-else>先补充信息，尚未开始检索或规划。</p></details>
  </section>
</template>
<style scoped>
.constraint-summary { margin-bottom:20px;padding:12px 17px;border:1px solid #e5e9de;border-radius:11px;background:#fcfdf8; }
.context-fields { display:flex;gap:12px;align-items:center;flex-wrap:wrap;font-size:10px; }
.context-label { color:#97a08d;margin-right:7px; }
.context-chip { color:#a6ae9c; } .context-chip.confirmed { color:#54743e; }
.updating { margin-left:auto;color:#9a9b76; }
.context-details { margin-top:8px;font-size:10px;color:#87957a; }
summary { cursor:pointer; }
.constraint-tags { display:flex;flex-wrap:wrap;gap:6px;margin-top:11px; }
.constraint-tags span { padding:3px 7px;border-radius:4px;background:#edf2e5;color:#667d52;overflow-wrap:anywhere; }
.context-details p { margin:8px 0 0; }
</style>
