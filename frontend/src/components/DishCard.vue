<script setup lang="ts">
import { computed } from 'vue'
import type { MenuItem } from '../types'

const props = defineProps<{ item: MenuItem; busy: boolean; changed?: boolean }>()
defineEmits<{ details: [item: MenuItem]; replace: [slot: number] }>()
const previewIngredients = computed(() => props.item.ingredients.slice(0, 5))
const sourcePreview = computed(() => {
  const nutrition = props.item.nutrition
  if (!nutrition) return '查看食材与烹饪方法'
  if (nutrition.protein_sources.length) return `蛋白质来源 · ${nutrition.protein_sources.slice(0, 2).join('、')}`
  if (nutrition.dietary_fiber.length) return `纤维来源 · ${nutrition.dietary_fiber.slice(0, 2).join('、')}`
  if (nutrition.carbohydrate_sources.length) return `碳水来源 · ${nutrition.carbohydrate_sources.slice(0, 2).join('、')}`
  return '营养来源待补充'
})
</script>

<template>
  <article class="dish-card" :class="{ 'is-changed': changed }" :data-testid="`menu-card-${item.slot}`">
    <div class="dish-visual" :class="`visual-${((item.slot - 1) % 3) + 1}`" role="img" :aria-label="`${item.name}的抽象餐盘占位图，非实拍`">
      <span class="dish-number">{{ String(item.slot).padStart(2, '0') }}</span>
      <span v-if="changed" class="changed-label">本轮已换新</span>
      <div class="plate" aria-hidden="true">
        <div class="plate-food food-green"></div><div class="plate-food food-orange"></div>
        <div class="plate-food food-cream"></div><span class="leaf leaf-one"></span><span class="leaf leaf-two"></span>
      </div>
      <span class="visual-note">菜品示意 · 非实拍</span>
    </div>
    <div class="dish-body">
      <div class="dish-tags" v-if="item.card?.badges.length">
        <span v-for="badge in item.card.badges.slice(0, 3)" :key="badge">{{ badge }}</span>
      </div>
      <h3>{{ item.card?.title || item.name }}</h3>
      <p class="dish-subtitle" v-if="item.card?.subtitle">{{ item.card.subtitle }}</p>
      <p class="dish-reason">{{ item.reasons[0] || '来自真实菜谱库，查看详情了解食材与做法。' }}</p>
      <div class="ingredients-preview" aria-label="主要食材">
        <span v-for="(ingredient, index) in previewIngredients" :key="`${ingredient}-${index}`">{{ ingredient }}</span>
        <span v-if="item.ingredients.length > 5">+{{ item.ingredients.length - 5 }}</span>
      </div>
      <p class="source-preview"><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M15.5 3.5c-6-.2-10 2.2-10 6.5 0 2.6 1.5 4 3.7 4 4.5 0 6.7-4.4 6.3-10.5Z" stroke="currentColor" stroke-width="1.4"/><path d="m4.5 16 6-7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>{{ sourcePreview }}</p>
      <div class="dish-actions">
        <button type="button" class="detail-button" :data-testid="`recipe-details-${item.slot}`" @click="$emit('details', item)">查看做法与来源 <span aria-hidden="true">↗</span></button>
        <button type="button" class="replace-button" :data-testid="`replace-dish-${item.slot}`" :disabled="busy" :aria-label="`换掉第${item.slot}道菜：${item.name}`" @click="$emit('replace', item.slot)"><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M15.5 7a6 6 0 0 0-10-1.5L3 8m0 0V3m0 5h5M4.5 13a6 6 0 0 0 10 1.5L17 12m0 0v5m0-5h-5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>换一道</button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.dish-card{overflow:hidden;min-width:0;background:#fff;border:1px solid #e1e6df;border-radius:19px;transition:border-color .2s,box-shadow .2s;display:flex;flex-direction:column}
.dish-card:hover{border-color:#b6c7b7;box-shadow:0 8px 22px #214b3d08}.dish-card.is-changed{border-color:#73a685;box-shadow:0 0 0 2px #83ad8e1c}
.dish-visual{height:155px;position:relative;display:grid;place-items:center;overflow:hidden;background:radial-gradient(ellipse at 70% 20%,#f6f0d9 0,transparent 60%),#e9ede1}.visual-2{background:radial-gradient(ellipse at 30% 30%,#e6edd9 0,transparent 70%),#e0e8dc}.visual-3{background:radial-gradient(ellipse at 70% 20%,#f3e5da 0,transparent 70%),#eee8df}.dish-number{position:absolute;top:13px;left:16px;color:#506652;font-size:12px;letter-spacing:.12em;font-weight:600}.visual-note{position:absolute;bottom:9px;right:12px;font-size:10px;letter-spacing:.025em;color:#526451;background:#ffffffa6;padding:3px 7px;border-radius:20px}.changed-label{position:absolute;top:11px;right:11px;border-radius:20px;padding:4px 8px;background:#214b3d;color:white;font-size:10px;z-index:1}.plate{position:relative;width:124px;height:124px;border-radius:50%;background:#faf9f0;box-shadow:inset 0 0 0 10px #fffffc,inset 0 0 0 12px #dddcd247,9px 13px 17px #56634b16;transform:rotate(-13deg)}.plate-food{position:absolute;border-radius:44% 49% 46% 52%;box-shadow:inset -4px -5px 0 #ffffff22}.food-green{width:41px;height:49px;left:24px;top:23px;background:#638b61;transform:rotate(-24deg)}.food-orange{width:34px;height:35px;left:64px;top:25px;background:#c88853;transform:rotate(14deg)}.food-cream{width:49px;height:33px;left:49px;top:70px;background:#e0c899;transform:rotate(-15deg)}.leaf{position:absolute;width:21px;height:10px;border-radius:100% 0 100% 0;background:#476849}.leaf-one{left:29px;top:80px;transform:rotate(-28deg)}.leaf-two{left:74px;top:62px;transform:rotate(20deg)}.visual-2 .plate{transform:rotate(20deg)}.visual-2 .food-orange{background:#aebc6c}.visual-2 .food-cream{background:#d9ce8c}.visual-3 .plate{transform:rotate(-32deg)}.visual-3 .food-green{background:#a4b778}.visual-3 .food-orange{background:#c69072}
.dish-body{padding:18px;display:flex;flex-direction:column;flex:1;min-width:0}.dish-tags{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:9px}.dish-tags span{background:#f0f4ec;color:#546c50;font-size:10px;border-radius:4px;padding:2px 6px}.dish-body h3{font-size:17px;line-height:1.5;letter-spacing:.015em;margin:0;color:#273e32;font-weight:650;overflow-wrap:anywhere}.dish-subtitle{font-size:11px;color:#879186;margin:4px 0 0}.dish-reason{font-size:12px;line-height:1.75;color:#626e60;margin:10px 0;overflow-wrap:anywhere}.ingredients-preview{display:flex;gap:5px;flex-wrap:wrap;margin:0 0 13px}.ingredients-preview span{font-size:11px;color:#6a7466;border:1px solid #e9ece5;border-radius:5px;padding:2px 6px;overflow-wrap:anywhere}.source-preview{margin:auto 0 12px;font-size:11px;line-height:1.6;color:#5a7457;display:flex;align-items:flex-start;gap:4px;overflow-wrap:anywhere}.source-preview svg{width:16px;height:16px;flex:0 0 16px;margin-top:1px}.dish-actions{border-top:1px solid #ecefe9;padding-top:12px;display:flex;justify-content:space-between;align-items:center;gap:6px}.dish-actions button{font:inherit;font-size:11px;cursor:pointer;white-space:nowrap}.detail-button{background:transparent;border:0;color:#345741;padding:5px 0;text-align:left}.detail-button span{margin-left:3px}.replace-button{display:flex;align-items:center;gap:4px;border:1px solid #dfe6db;background:#fafbf8;color:#52674e;padding:6px 8px;border-radius:7px}.replace-button svg{width:14px;height:14px}.replace-button:hover:not(:disabled){background:#edf3e8}.replace-button:disabled{opacity:.45;cursor:wait}.dish-actions button:focus-visible{outline:2px solid #648a66;outline-offset:3px}
@media(max-width:520px){.dish-visual{height:148px}.dish-body{padding:16px}.dish-body h3{font-size:18px}.dish-actions button{font-size:12px}}
@media(prefers-reduced-motion:reduce){.dish-card{transition:none}}
</style>
