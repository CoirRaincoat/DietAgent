<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { ChatResult, MenuItem } from '../types'
import DishCard from './DishCard.vue'
import DishDetail from './DishDetail.vue'
import NutritionPanel from './NutritionPanel.vue'

const props = defineProps<{ result: ChatResult | null; busy: boolean }>()
defineEmits<{ replace: [slot: number]; explain: [] }>()
const activeTab = ref<'menu' | 'nutrition'>('menu')
const selectedDish = ref<MenuItem | null>(null)
const changedSlots = ref<number[]>([])
const menuTab = ref<HTMLButtonElement | null>(null)
const nutritionTab = ref<HTMLButtonElement | null>(null)
const menu = computed(() => props.result?.status === 'ok' && props.result.conversation_state.menu_valid ? props.result.menu : [])
const analysis = computed(() => menu.value.length ? props.result?.nutrition_analysis || null : null)
const mealSummary = computed(() => {
  if (!props.result || !menu.value.length) return '了解需求，再安排一餐'
  const constraints = props.result.conversation_state.constraints
  return `${constraints.people} 人 · ${constraints.meal_type} · ${menu.value.length} 道菜`
})
watch(() => props.result, (current, previous) => {
  selectedDish.value = null
  changedSlots.value = current?.status === 'ok' && previous?.status === 'ok' && current.conversation_state.session_id === previous.conversation_state.session_id
    ? current.menu.filter(item => previous.menu.some(before => before.slot === item.slot && before.recipe_id !== item.recipe_id)).map(item => item.slot) : []
  if (!current || current.status !== 'ok') activeTab.value = 'menu'
})
function changeTab(tab: 'menu' | 'nutrition', focus = false) {
  activeTab.value = tab
  if (focus) void nextTick(() => (tab === 'menu' ? menuTab.value : nutritionTab.value)?.focus())
}
function tabKeys(event: KeyboardEvent) {
  if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
    event.preventDefault()
    changeTab(event.key === 'Home' ? 'menu' : event.key === 'End' ? 'nutrition' : activeTab.value === 'menu' ? 'nutrition' : 'menu', true)
  }
}
</script>

<template>
  <section class="menu-panel" aria-label="本餐菜单与营养分析">
    <header class="panel-heading"><div><span class="panel-eyebrow">YOUR DAILY TABLE</span><h2>为你安排的这一餐</h2></div><span class="meal-summary">{{ mealSummary }}</span></header>
    <div class="menu-tabs" role="tablist" aria-label="菜单展示方式" @keydown="tabKeys">
      <button ref="menuTab" id="menu-tab" type="button" role="tab" :aria-selected="activeTab === 'menu'" aria-controls="menu-tabpanel" :tabindex="activeTab === 'menu' ? 0 : -1" @click="changeTab('menu')"><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M3 4v4a2 2 0 0 0 4 0V4M5 4v13M15 3c-2 1-3 4-3 7h3m0-7v14" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>本餐菜单<span v-if="menu.length" class="menu-count">{{ menu.length }}</span></button>
      <button ref="nutritionTab" id="nutrition-tab" type="button" role="tab" :aria-selected="activeTab === 'nutrition'" aria-controls="nutrition-tabpanel" :tabindex="activeTab === 'nutrition' ? 0 : -1" @click="changeTab('nutrition')"><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M16 3C7 3 4 7 5 12c1 4 7 3 9-1 1-2 2-5 2-8Z" stroke="currentColor" stroke-width="1.3"/><path d="M4 17 12 8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>营养分析</button>
    </div>
    <div v-if="activeTab === 'menu'" id="menu-tabpanel" role="tabpanel" aria-labelledby="menu-tab" class="menu-content" :aria-busy="busy">
      <template v-if="menu.length">
        <div class="menu-intro"><p>来自真实菜谱库，按本餐需求筛选。</p><button type="button" :disabled="busy" @click="$emit('explain')">解释这份菜单 <span aria-hidden="true">↗</span></button></div>
        <div class="dish-grid"><DishCard v-for="item in menu" :key="`${item.slot}-${item.recipe_id}`" :item="item" :busy="busy" :changed="changedSlots.includes(item.slot)" @details="selectedDish = $event" @replace="$emit('replace', $event)" /></div>
        <div class="menu-footnote"><svg viewBox="0 0 18 18" fill="none" aria-hidden="true"><path d="M9 2 3 4v5c0 3 4 5 6 6 2-1 6-3 6-6V4L9 2Z" stroke="currentColor" stroke-width="1.2"/><path d="m6 8 2 2 4-4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg><span>食材与步骤可追溯到原始菜谱；营养仅作定性解释。</span></div>
        <details v-if="result?.replacement_suggestions.length" class="suggestions"><summary>探索替换思路 <span>{{ result.replacement_suggestions.length }} 个库内候选</span></summary><p class="suggestion-note">候选供查看；“换一道”会让 Agent 为该位置重新选择，不能指定候选 ID。</p><div class="suggestion-grid"><article v-for="candidate in result.replacement_suggestions" :key="candidate.recipe_id"><span class="candidate-slot">第 {{ candidate.slot }} 道的候选</span><h3>{{ candidate.name }}</h3><p>{{ candidate.replacement_reason || candidate.reasons[0] || '来自相同约束下的候选菜谱。' }}</p><button type="button" @click="selectedDish = candidate">查看候选详情 <span aria-hidden="true">↗</span></button></article></div></details>
      </template>
      <div v-else class="menu-empty">
        <div class="empty-composition" aria-hidden="true"><div class="empty-orbit orbit-one"></div><div class="empty-orbit orbit-two"></div><div class="empty-plate"><svg viewBox="0 0 70 70" fill="none"><path d="M49 20c-23-1-32 11-25 23 10 9 25-1 25-23Z" stroke="currentColor" stroke-width="1.6"/><path d="M20 52 39 31" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="m30 42-1-9m6 3 8 1" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg></div><span class="empty-seed seed-one"></span><span class="empty-seed seed-two"></span><span class="empty-seed seed-three"></span></div>
        <span class="empty-eyebrow">A MEAL THAT FITS YOU</span>
        <h3>{{ result?.status === 'clarification_required' ? '先了解你，再安排这一餐' : result?.status === 'no_feasible_menu' ? '一起调整一下本餐需求' : '你的下一餐，可以更合心意' }}</h3>
        <p>{{ result?.status === 'clarification_required' ? '还有信息需要确认。回答对话中的问题后，Agent 会继续为你筛选菜谱。' : result?.status === 'no_feasible_menu' ? '当前条件下没有找到可用菜单。请根据对话提示补充或调整需求。' : '告诉我想吃什么、为谁准备。菜单、食材和营养解释会在这里逐一呈现。' }}</p>
        <div class="empty-features"><span>真实菜谱</span><i aria-hidden="true"></i><span>记住忌口</span><i aria-hidden="true"></i><span>解释有据</span></div>
      </div>
    </div>
    <div v-else id="nutrition-tabpanel" role="tabpanel" aria-labelledby="nutrition-tab" class="nutrition-content"><NutritionPanel :analysis="analysis" /></div>
    <DishDetail :item="selectedDish" @close="selectedDish = null" />
  </section>
</template>

<style scoped>
.menu-panel{min-width:0;color:#2d4b38}.panel-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:22px}.panel-eyebrow{font-size:9px;letter-spacing:.18em;font-weight:500;color:#819375}.panel-heading h2{font-size:23px;line-height:1.5;letter-spacing:.025em;font-weight:600;margin:6px 0 0}.meal-summary{font-size:11px;color:#8b987e;line-height:1.7;text-align:right;padding-bottom:4px;white-space:nowrap}.menu-tabs{display:flex;gap:25px;border-bottom:1px solid #dfe6d7;margin-bottom:21px}.menu-tabs>button{display:flex;align-items:center;gap:6px;background:none;border:0;border-bottom:2px solid transparent;padding:11px 0 13px;color:#98a38c;font-size:12px;cursor:pointer;position:relative;bottom:-1px;white-space:nowrap}.menu-tabs>button[aria-selected=true]{color:#345c42;border-bottom-color:#4f7a52;font-weight:600}.menu-tabs svg{width:18px;height:18px}.menu-count{display:inline-grid;place-items:center;border-radius:5px;background:#e6eddc;color:#779266;font-size:10px;min-width:18px;height:18px;margin-left:2px}.menu-intro{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:15px}.menu-intro p{margin:0;color:#8c987f;font-size:11px;line-height:1.7}.menu-intro button{border:0;background:transparent;font-size:11px;color:#59794b;padding:4px 0;white-space:nowrap;cursor:pointer}.menu-intro button:disabled{opacity:.4;cursor:wait}.dish-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:15px;align-items:stretch}.menu-footnote{display:flex;align-items:flex-start;gap:6px;font-size:10px;line-height:1.8;color:#99a48c;margin:17px 0 22px}.menu-footnote svg{width:15px;height:15px;flex:0 0 15px;margin-top:1px}.suggestions{border-top:1px solid #dfe6d7;padding-top:16px;color:#6b805a;font-size:12px}.suggestions>summary{cursor:pointer;line-height:1.8}.suggestions>summary>span{font-size:10px;color:#95a285;float:right}.suggestion-note{font-size:10px;line-height:1.8;color:#95a285;margin:10px 0 12px}.suggestion-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.suggestion-grid article{padding:14px;background:#f9fbf5;border:1px solid #e1e8d9;border-radius:12px;min-width:0}.candidate-slot{font-size:9px;color:#9eab8d}.suggestion-grid h3{font-size:13px;line-height:1.6;font-weight:600;margin:6px 0;color:#557047;overflow-wrap:anywhere}.suggestion-grid p{font-size:11px;color:#859777;line-height:1.7;overflow-wrap:anywhere}.suggestion-grid button{background:none;border:0;color:#6d8a5e;font-size:10px;padding:4px 0;cursor:pointer}.menu-empty{text-align:center;padding:39px 20px 35px;min-height:430px;display:flex;flex-direction:column;align-items:center;justify-content:center}.empty-composition{height:170px;width:210px;position:relative;display:grid;place-items:center;margin-bottom:17px}.empty-orbit{position:absolute;border:1px solid #dce5d144;border-radius:50%;transform:rotate(-25deg)}.orbit-one{width:200px;height:143px;border-color:#dce5d1aa}.orbit-two{width:150px;height:185px;border-color:#dce5d155;transform:rotate(28deg)}.empty-plate{position:relative;width:130px;height:130px;border-radius:50%;display:grid;place-items:center;background:#fafcf5;box-shadow:inset 0 0 0 10px #fff,inset 0 0 0 11px #e6ebdd,5px 15px 25px #58753c0c}.empty-plate svg{width:66px;height:66px;color:#a4ba8b}.empty-seed{position:absolute;background:#b8c6a0}.seed-one{width:18px;height:8px;border-radius:90% 0 90% 0;top:29px;right:28px;transform:rotate(-22deg)}.seed-two{width:6px;height:6px;border-radius:50%;background:#dbbc8d;bottom:32px;left:26px}.seed-three{width:9px;height:4px;border-radius:90% 0 90% 0;bottom:39px;right:37px;transform:rotate(24deg)}.empty-eyebrow{font-size:9px;letter-spacing:.15em;color:#acb79e}.menu-empty h3{font-size:21px;line-height:1.6;font-weight:500;color:#59734d;margin:11px 0 12px;letter-spacing:.02em}.menu-empty>p{font-size:12px;line-height:1.9;max-width:340px;margin:0;color:#94a085}.empty-features{display:flex;gap:10px;align-items:center;justify-content:center;margin-top:28px;font-size:10px;color:#a6b098}.empty-features i{height:3px;width:3px;background:#bec9b3;border-radius:50%}.nutrition-content{min-width:0}.menu-panel button:focus-visible,.suggestions>summary:focus-visible{outline:2px solid #72965f;outline-offset:3px}
@media(min-width:1500px){.dish-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:650px){.panel-heading{margin-bottom:17px}.panel-heading h2{font-size:21px}.meal-summary{font-size:10px}.menu-tabs{gap:22px}.menu-empty{padding:28px 12px;min-height:340px}.empty-composition{transform:scale(.85);margin-bottom:0;height:150px}.menu-empty h3{font-size:19px}.menu-intro{align-items:flex-start}.dish-grid{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}}
@media(max-width:520px){.panel-heading{align-items:flex-start;flex-direction:column;gap:4px}.meal-summary{padding:0;text-align:left}.dish-grid{grid-template-columns:1fr}.suggestion-grid{grid-template-columns:1fr}.suggestions>summary>span{float:none;margin-left:9px}.menu-empty>p{max-width:270px}.empty-features{font-size:9px}}
</style>
