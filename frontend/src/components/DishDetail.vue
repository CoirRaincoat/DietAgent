<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { MenuItem } from '../types'

const props = defineProps<{ item: MenuItem | null }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
watch(() => props.item, async (item) => {
  await nextTick()
  if (item && dialog.value && !dialog.value.open) dialog.value.showModal()
  if (!item && dialog.value?.open) dialog.value.close()
}, { immediate: true })
function backdrop(event: MouseEvent) {
  if (event.target === event.currentTarget) emit('close')
}
const roleLabels: Record<string, string> = { protein: '蛋白质', carbohydrate: '碳水', fat: '脂肪', dietary_fiber: '膳食纤维' }
</script>

<template>
  <dialog ref="dialog" class="recipe-dialog" aria-labelledby="recipe-detail-title" data-testid="recipe-detail-dialog" @cancel.prevent="emit('close')" @click="backdrop">
    <div class="detail-shell" v-if="item">
      <header class="detail-header">
        <div><span class="detail-eyebrow">RECIPE DETAILS · 菜谱详情</span><h2 id="recipe-detail-title">{{ item.name }}</h2><p>{{ item.card?.subtitle || item.source }}</p></div>
        <button type="button" class="close-button" aria-label="关闭菜谱详情" autofocus @click="emit('close')"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button>
      </header>
      <div class="detail-content">
        <section class="detail-section" v-if="item.reasons.length">
          <h3>为什么推荐这道菜</h3><ul class="reason-list"><li v-for="(reason, index) in item.reasons" :key="index">{{ reason }}</li></ul>
        </section>
        <section class="detail-section">
          <h3>食材与原始用量</h3>
          <div class="ingredient-list" v-if="item.ingredient_details.length">
            <div v-for="(ingredient, index) in item.ingredient_details" :key="index" class="ingredient-row"><strong>{{ ingredient.name }}</strong><span>{{ ingredient.raw || (ingredient.quantity !== null ? `${ingredient.quantity}${ingredient.unit || ''}` : '用量未记录') }}</span></div>
          </div>
          <p v-else class="muted">{{ item.ingredients.join('、') || '食材信息不足' }}</p>
          <p class="field-note">按菜谱原文展示；未根据用餐人数推算份量。</p>
        </section>
        <section class="detail-section">
          <h3>烹饪步骤</h3>
          <ol class="cooking-list" v-if="item.cooking_steps.length"><li v-for="step in item.cooking_steps" :key="step.number"><span class="step-number">{{ step.number }}</span><p>{{ step.description }}</p></li></ol>
          <p v-else class="raw-steps">{{ item.steps || '菜谱未记录烹饪步骤。' }}</p>
        </section>
        <section class="detail-section" v-if="item.nutrition">
          <h3>这道菜的营养来源</h3>
          <dl class="nutrition-sources"><div><dt>蛋白质</dt><dd>{{ item.nutrition.protein_sources.join('、') || '未识别到明确来源' }}</dd></div><div><dt>碳水</dt><dd>{{ item.nutrition.carbohydrate_sources.join('、') || '未识别到明确来源' }}</dd></div><div><dt>脂肪</dt><dd>{{ item.nutrition.fat_sources.join('、') || '未识别到明确来源' }}</dd></div><div><dt>膳食纤维</dt><dd>{{ item.nutrition.dietary_fiber.join('、') || '未识别到明确来源' }}</dd></div></dl>
          <p class="field-note">热量与营养素含量：数据不足，暂无法准确评估。未识别到来源不代表不含该营养素。</p>
          <details class="trace-details" v-if="item.nutrition.ingredient_contributions.length"><summary>查看食材贡献依据</summary><div v-for="(entry, index) in item.nutrition.ingredient_contributions" :key="index" class="contribution"><strong>{{ entry.ingredient_name }}</strong><span>{{ entry.roles.map(role => roleLabels[role] || role).join(' / ') }}</span><p>{{ entry.explanation }}</p><small>来源行 {{ entry.source_row }} · {{ entry.quantity_recorded ? '有用量记录，未核算摄入量' : '用量未记录' }}</small></div></details>
          <ul v-if="item.nutrition.suitable_reasons.length" class="reason-list nutrition-reasons"><li v-for="(reason, index) in item.nutrition.suitable_reasons" :key="index">{{ reason }}</li></ul>
          <div v-if="item.nutrition.risks.length" class="risk-box"><strong>需要留意</strong><p v-for="(risk, index) in item.nutrition.risks" :key="index">{{ risk.message }}</p></div>
          <details class="trace-details" v-if="item.nutrition.limitations.length"><summary>数据局限</summary><ul class="reason-list"><li v-for="(limitation, index) in item.nutrition.limitations" :key="index">{{ limitation }}</li></ul></details>
        </section>
        <section class="detail-section source-section">
          <h3>菜谱来源</h3><p>{{ item.source }}</p>
          <dl v-if="item.provenance"><div><dt>菜谱 ID</dt><dd>{{ item.provenance.recipe_id }}</dd></div><div><dt>数据行</dt><dd>{{ item.provenance.source_row }}</dd></div><div><dt>内容指纹</dt><dd class="fingerprint">{{ item.provenance.fingerprint }}</dd></div></dl>
          <p v-else class="muted">菜谱 ID：{{ item.recipe_id }}；更多来源信息未提供。</p>
          <p class="field-note">食材、步骤和来源均来自后端返回；展示图为抽象占位图。</p>
        </section>
      </div>
      <footer class="detail-footer"><button type="button" @click="emit('close')">返回本餐菜单</button></footer>
    </div>
  </dialog>
</template>

<style scoped>
.recipe-dialog{max-width:700px;width:calc(100% - 32px);max-height:88dvh;padding:0;border:1px solid #e0e7db;border-radius:22px;color:#2d4234;background:#fdfefa;box-shadow:0 30px 100px #132b3340;overflow:hidden}.recipe-dialog::backdrop{background:#172b2566;backdrop-filter:blur(4px)}.detail-shell{max-height:88dvh;display:flex;flex-direction:column}.detail-header{padding:25px 28px 20px;display:flex;justify-content:space-between;align-items:flex-start;gap:16px;background:#eef3e9;border-bottom:1px solid #e1e8dc;flex-shrink:0}.detail-eyebrow{font-size:10px;letter-spacing:.12em;color:#6b8067}.detail-header h2{font-size:25px;margin:7px 0;line-height:1.5;overflow-wrap:anywhere}.detail-header p{font-size:12px;margin:0;color:#73816d}.close-button{display:grid;place-items:center;width:32px;height:32px;border:1px solid #dbe4d4;border-radius:50%;background:#ffffff91;color:#496042;cursor:pointer;flex-shrink:0}.close-button svg{width:18px;height:18px}.close-button:hover{background:#fff}.detail-content{overflow-y:auto;padding:3px 28px 14px;overscroll-behavior:contain}.detail-section{padding:21px 0;border-bottom:1px solid #e9ede5}.detail-section:last-child{border-bottom:0}.detail-section h3{font-size:14px;font-weight:650;margin:0 0 13px;color:#2f503a}.reason-list{padding-left:18px;margin:0;color:#60705a;font-size:12px;line-height:1.9}.reason-list li+li{margin-top:5px}.ingredient-list{border:1px solid #e6ebdf;border-radius:10px;overflow:hidden}.ingredient-row{display:grid;grid-template-columns:1fr 2fr;align-items:start;gap:14px;padding:9px 12px;font-size:12px}.ingredient-row:nth-child(odd){background:#f5f7f0}.ingredient-row strong{font-weight:500;overflow-wrap:anywhere}.ingredient-row span{color:#71806a;text-align:right;white-space:pre-wrap;overflow-wrap:anywhere}.field-note{font-size:11px;line-height:1.8;color:#7d8774;margin:12px 0 0}.cooking-list{padding:0;margin:0;list-style:none}.cooking-list li{display:flex;gap:12px}.cooking-list li+li{margin-top:15px}.step-number{display:grid;place-items:center;border-radius:50%;width:24px;height:24px;flex:0 0 24px;background:#edf2e7;font-size:11px;color:#55734b}.cooking-list p,.raw-steps{font-size:12px;line-height:1.9;white-space:pre-wrap;margin:0;color:#5a6b53;overflow-wrap:anywhere}.nutrition-sources{margin:0;display:grid;grid-template-columns:1fr 1fr;gap:9px}.nutrition-sources div{background:#f2f6ed;border-radius:9px;padding:12px}.nutrition-sources dt{font-size:10px;color:#76866e;margin-bottom:5px}.nutrition-sources dd{margin:0;font-size:12px;line-height:1.7;overflow-wrap:anywhere}.trace-details{margin-top:15px;font-size:12px;color:#56704b}.trace-details summary{cursor:pointer;padding:4px 0}.trace-details>.reason-list{margin-top:8px}.contribution{padding:11px 0;border-top:1px solid #e8eddf}.contribution:first-of-type{margin-top:9px}.contribution>strong{font-weight:600;margin-right:10px}.contribution>span{font-size:10px;background:#edf3e7;border-radius:4px;padding:2px 5px}.contribution p{font-size:12px;line-height:1.8;margin:7px 0;color:#6b795f}.contribution small{font-size:10px;color:#88917f}.nutrition-reasons{margin-top:14px}.risk-box{background:#fbf4e9;border:1px solid #efe4d3;border-radius:10px;padding:13px;margin-top:15px;color:#8d6c39;font-size:12px}.risk-box strong{font-weight:600}.risk-box p{line-height:1.8;margin:7px 0 0}.source-section>p{font-size:12px;color:#718169;line-height:1.7}.source-section dl{margin:0;display:grid;gap:8px}.source-section dl>div{display:grid;grid-template-columns:65px 1fr;gap:10px;font-size:11px;line-height:1.7}.source-section dt{color:#7e8976}.source-section dd{margin:0;color:#5d7053;overflow-wrap:anywhere}.source-section dd.fingerprint{font-family:ui-monospace,Consolas,monospace;font-size:10px;word-break:break-all}.muted{font-size:12px;color:#7d8774;line-height:1.8}.detail-footer{border-top:1px solid #e7ecdf;background:#f9fbf4;display:flex;justify-content:flex-end;padding:13px 25px;flex-shrink:0}.detail-footer button{background:#214b3d;color:white;border:0;padding:9px 18px;border-radius:8px;font-size:12px;cursor:pointer}.recipe-dialog button:focus-visible,.trace-details summary:focus-visible{outline:2px solid #638e5a;outline-offset:3px}
@media(max-width:520px){.detail-header{padding:20px}.detail-header h2{font-size:21px}.detail-content{padding:0 20px 12px}.nutrition-sources{grid-template-columns:1fr}.ingredient-row{grid-template-columns:1fr 1.5fr}.recipe-dialog{width:calc(100% - 20px);max-height:92dvh}.detail-shell{max-height:92dvh}}
</style>
