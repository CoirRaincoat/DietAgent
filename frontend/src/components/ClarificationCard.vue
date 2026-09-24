<script setup lang="ts">
import type { ClarificationQuestion } from '../types'
defineProps<{ questions: ClarificationQuestion[]; busy: boolean }>()
const emit = defineEmits<{ answer: [text: string] }>()
function choose(option: string) {
  if (option === '补充忌口食材') { document.querySelector<HTMLTextAreaElement>('#chat-input')?.focus(); return }
  emit('answer', option)
}
</script>
<template>
  <div v-if="questions.length" class="clarification-card" aria-label="补充用餐信息">
    <div class="clarification-intro"><span aria-hidden="true">◌</span><strong>再了解你一点</strong><span>点选即可回答</span></div>
    <fieldset v-for="question in questions" :key="question.field" :data-testid="`clarification-${question.field}`" :disabled="busy">
      <legend>{{ question.prompt }}</legend>
      <div v-if="question.options.length" class="clarification-options"><button v-for="option in question.options" :key="option" type="button" @click="choose(option)">{{ option }}</button></div>
      <p v-else>请在下方输入框补充具体信息。</p>
    </fieldset>
  </div>
</template>
<style scoped>
.clarification-card { background: #fbfdf7; border: 1px solid #dfe8d4; border-radius: 12px; padding: 16px 14px; margin-top: 12px; }
.clarification-intro { display:flex;align-items:center;gap:7px;font-size:12px;color:#557341;margin-bottom:16px; }
.clarification-intro>span:first-child { font-size:23px;line-height:1; }
.clarification-intro>span:last-child { margin-left:auto;color:#a0ac94;font-size:9px; }
fieldset { border:0;padding:0;margin:0 0 15px;min-width:0; } fieldset:last-child { margin-bottom:0; }
legend { font-size:11px;color:#738367;margin-bottom:8px;line-height:1.7; }
.clarification-options { display:flex;flex-wrap:wrap;gap:7px; }
button { border:1px solid #dce5d1;color:#52703d;background:#fff;border-radius:7px;padding:5px 10px;font-size:11px; }
button:not(:disabled):hover { background:#e6eeda;border-color:#9cb389; }
p { font-size:11px;color:#8a997d;margin:0; }
</style>
