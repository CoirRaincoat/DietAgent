<script setup lang="ts">
import { computed } from 'vue'
import { demoCases } from '../demoCases'
import type { ChatResult } from '../types'
const props = defineProps<{ busy: boolean; ready: boolean; selectedCase: number | null; result: ChatResult | null; completed: boolean }>()
const emit = defineEmits<{ start:[id:number]; next:[text:string] }>()
const current = computed(() => demoCases.find(item => item.id === props.selectedCase))
const needsContext = computed(() => props.result?.status === 'clarification_required')
const nextText = computed(() => current.value?.next || '')
</script>
<template>
  <section class="demo-guide" aria-label="比赛演示案例">
    <div class="demo-heading"><div><span class="eyebrow">THREE MINUTES, ONE BETTER MEAL</span><h2>三个小场景，认识你的膳食伙伴。</h2></div><span class="demo-label">真实交互 · 合成资料</span></div>
    <div class="demo-cases"><button v-for="item in demoCases" :key="item.id" :data-testid="`demo-case-${item.id}`" :class="{selected: selectedCase === item.id}" :disabled="busy || !ready" @click="emit('start', item.id)"><span class="case-number">{{ item.icon }}</span><span><strong>{{ item.title }}</strong><small>{{ item.subtitle }}</small></span><span class="case-arrow" aria-hidden="true">↗</span></button></div>
    <div v-if="current" class="demo-guidance"><div><strong>{{ completed ? '这一步已完成' : '接下来，试试看' }}</strong><p>{{ current.focus }}</p></div><button v-if="result?.status === 'ok' && !completed" data-testid="demo-next-step" :disabled="busy || !ready" @click="emit('next', nextText)">{{ current.action }}<span aria-hidden="true"> →</span></button><span v-else-if="busy" class="demo-wait" role="status">正在等待实际回复…</span><span v-else-if="needsContext" class="demo-wait">请先回答下方澄清问题</span><span v-else-if="result?.status === 'no_feasible_menu'" class="demo-wait">请先调整本餐需求</span><span v-else-if="completed" class="demo-complete">✓ 可继续对话或查看营养分析</span></div>
    <p class="demo-caption">每次选择场景都会新建合成用户的对话；所有结果均由当前服务返回。也可以直接点选下方澄清选项。</p>
  </section>
</template>
<style scoped>
.demo-guide { background:#fdfef9;border:1px solid #e0e7d7;padding:23px;border-radius:16px;margin:0 0 24px; }
.demo-heading { display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px; }
h2 { font-size:18px;font-weight:500;margin:7px 0 0; }
.demo-heading .eyebrow { font-size:8px;letter-spacing:1.6px; }
.demo-label { color:#80966b;font-size:10px;white-space:nowrap; }
.demo-cases { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px; }
.demo-cases button { display:flex;text-align:left;align-items:center;gap:12px;border:1px solid #e1e7d8;border-radius:11px;padding:15px;background:#f5f8ee;color:#647e50; }
.demo-cases button.selected { background:#e5eed9;border-color:#aac18f;box-shadow:0 0 0 2px #aac18f16; }
.case-number { font-size:21px;font-weight:300;letter-spacing:1px;color:#afbd9d; }
.demo-cases strong { display:block;font-size:13px;font-weight:500; }
.demo-cases small { font-size:10px;color:#98a18d;display:block;margin-top:4px; }
.case-arrow { margin-left:auto;color:#82976b; }
.demo-guidance { display:flex;gap:18px;align-items:center;justify-content:space-between;margin-top:18px;border-top:1px solid #e6ebde;padding-top:15px; }
.demo-guidance strong { font-size:11px;font-weight:500;color:#5d754b; }
.demo-guidance p { margin:4px 0 0;font-size:10px;color:#8d9a7e;max-width:550px; }
.demo-guidance button { flex:none;border:0;border-radius:8px;background:#31533a;color:#fff;font-size:11px;padding:9px 13px; }
.demo-caption { font-size:9px;color:#a0a995;margin:13px 0 0; }
.demo-wait,.demo-complete { flex:none;color:#7c9367;font-size:11px; }
@media(max-width:900px) { .demo-cases button{padding:12px;gap:8px;} .case-number{font-size:17px;} .demo-cases small{font-size:9px;} }
@media(max-width:650px) { .demo-guide{padding:17px;} .demo-heading{align-items:flex-start;}h2{font-size:16px;}.demo-label{display:none;}.demo-cases{grid-template-columns:1fr;gap:8px;}.demo-cases small{font-size:10px;}.demo-guidance{align-items:flex-start;flex-direction:column;gap:10px;}.demo-guidance button{white-space:normal;text-align:left;} }
</style>
