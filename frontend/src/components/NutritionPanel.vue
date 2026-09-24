<script setup lang="ts">
import { computed } from 'vue'
import type { MenuNutrition } from '../types'

const props = defineProps<{ analysis: MenuNutrition | null }>()
const categories = computed(() => [
  { key: 'protein', title: '蛋白质', label: '构成来源', symbol: 'P', sources: props.analysis?.protein_sources || [], tone: 'sage' },
  { key: 'carbohydrate', title: '碳水化合物', label: '构成来源', symbol: 'C', sources: props.analysis?.carbohydrate_sources || [], tone: 'wheat' },
  { key: 'fat', title: '脂肪', label: '构成来源', symbol: 'F', sources: props.analysis?.fat_sources || [], tone: 'peach' },
  { key: 'fiber', title: '膳食纤维', label: '构成来源', symbol: 'Fi', sources: props.analysis?.dietary_fiber || [], tone: 'olive' },
])
const statusLabels = { preference_match: '偏好匹配', caution: '需要留意', insufficient_data: '数据不足' }
const roleLabels: Record<string, string> = { protein: '蛋白质', carbohydrate: '碳水', fat: '脂肪', dietary_fiber: '膳食纤维' }
function safeUrl(url: string) { return /^https?:\/\//i.test(url) ? url : undefined }
</script>

<template>
  <div class="nutrition-panel" data-testid="nutrition-summary">
    <template v-if="analysis">
      <header class="nutrition-heading"><span class="section-kicker">KNOW YOUR PLATE</span><h2>看懂这一餐的营养组成</h2><p>从食材与做法出发，了解来源、适合原因和需要留意的地方。</p></header>
      <div class="energy-card">
        <div class="energy-symbol" aria-hidden="true"><svg viewBox="0 0 28 28" fill="none"><path d="M15 3c1 7-5 7-2 13 2-1 4-3 4-6 4 4 6 7 4 11-3 7-14 5-15-2C5 13 10 9 15 3Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg></div>
        <div><span class="metric-label">本餐热量</span><strong>数据不足</strong><p>暂无法准确评估</p></div>
        <span class="qualitative-tag">定性分析</span>
      </div>
      <div class="nutrient-grid">
        <article v-for="category in categories" :key="category.key" class="nutrient-card" :class="`tone-${category.tone}`">
          <div class="nutrient-title"><span class="nutrient-symbol" aria-hidden="true">{{ category.symbol }}</span><h3>{{ category.title }}</h3></div>
          <p class="source-label">{{ category.label }}</p>
          <div class="source-chips" v-if="category.sources.length"><span v-for="(source, index) in category.sources" :key="index">{{ source }}</span></div>
          <p v-else class="missing-source">未识别到明确来源</p>
          <p class="amount-note">摄入量 <strong>数据不足</strong></p>
        </article>
      </div>
      <p class="nutrition-note">现有菜谱缺少完整份量与营养成分数据；来源提示不代表个人摄入达标，未识别到来源也不代表不含该营养素。</p>
      <section v-if="analysis.suitable_reasons.length" class="explanation-block">
        <h3><span class="section-dot"></span>适合原因</h3><ul class="explanation-list"><li v-for="(reason, index) in analysis.suitable_reasons" :key="index">{{ reason }}</li></ul>
      </section>
      <section v-if="analysis.goal_matches.length" class="explanation-block">
        <h3><span class="section-dot"></span>与你的健康目标如何关联</h3>
        <article v-for="(match, index) in analysis.goal_matches" :key="index" class="goal-card">
          <div class="goal-heading"><h4>{{ match.goal }}</h4><span class="match-status" :class="`match-${match.status}`">{{ statusLabels[match.status] }}</span></div>
          <ul class="explanation-list"><li v-for="(reason, reasonIndex) in match.reasons" :key="reasonIndex">{{ reason }}</li></ul>
          <p v-if="match.ingredient_names.length" class="goal-facts">相关食材：{{ match.ingredient_names.join('、') }}</p>
          <p v-if="match.methods.length" class="goal-facts">烹饪方式：{{ match.methods.join('、') }}</p>
          <p class="goal-limitation">{{ match.limitation }}</p>
          <div v-if="match.sources.length" class="reference-links"><span>参考依据</span><a v-for="source in match.sources" :key="source.source_id" :href="safeUrl(source.url)" target="_blank" rel="noopener noreferrer">{{ source.title }} <span aria-hidden="true">↗</span></a></div>
        </article>
      </section>
      <section v-if="analysis.risks.length" class="risk-section">
        <h3><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m10 3 7 13H3L10 3Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M10 8v3m0 2v.2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>这一餐需要留意</h3>
        <ul class="explanation-list"><li v-for="(risk, index) in analysis.risks" :key="index">{{ risk.message }}<span v-if="risk.ingredient_names.length" class="risk-ingredients">涉及食材：{{ risk.ingredient_names.join('、') }}</span></li></ul>
      </section>
      <details v-if="analysis.ingredient_contributions.length" class="nutrition-details"><summary>食材贡献与菜谱溯源 <span>{{ analysis.ingredient_contributions.length }} 条依据</span></summary><div class="contribution-list"><article v-for="(contribution, index) in analysis.ingredient_contributions" :key="index"><div><strong>{{ contribution.ingredient_name }}</strong><span>{{ contribution.roles.map(role => roleLabels[role] || role).join(' / ') }}</span></div><p>{{ contribution.explanation }}</p><small>菜谱 {{ contribution.recipe_id }} · 来源行 {{ contribution.source_row }} · {{ contribution.quantity_recorded ? '有用量记录，未核算摄入量' : '用量未记录' }}</small></article></div></details>
      <details v-if="analysis.limitations.length" class="nutrition-details"><summary>数据局限与解释范围</summary><ul class="explanation-list limitations"><li v-for="(limitation, index) in analysis.limitations" :key="index">{{ limitation }}</li></ul></details>
    </template>
    <div v-else class="nutrition-empty"><span class="empty-leaf" aria-hidden="true">◌</span><h2>先一起确定这一餐</h2><p>菜单生成后，将在这里展示食材营养来源、目标匹配与风险提示。</p><span>热量与营养素含量：数据不足，暂无法准确评估。</span></div>
  </div>
</template>

<style scoped>
.nutrition-panel{min-width:0}.nutrition-heading{margin-bottom:22px}.section-kicker{font-size:10px;letter-spacing:.16em;color:#7b8d71}.nutrition-heading h2{font-size:21px;letter-spacing:.015em;line-height:1.5;margin:8px 0 7px;color:#2c503b}.nutrition-heading p{font-size:12px;line-height:1.8;color:#7e8974;margin:0}.energy-card{border:1px solid #dce6d4;background:linear-gradient(110deg,#e8efdf,#f6f8f0);border-radius:16px;display:flex;align-items:center;gap:17px;padding:20px;position:relative;margin-bottom:13px}.energy-symbol{height:47px;width:47px;background:#fff9;border:1px solid #dde6d4;border-radius:13px;display:grid;place-items:center;color:#698458;flex-shrink:0}.energy-symbol svg{width:27px;height:27px}.metric-label{display:block;color:#6e7f60;font-size:11px;margin-bottom:5px}.energy-card strong{font-size:20px;letter-spacing:.03em;font-weight:600;color:#466237}.energy-card p{font-size:11px;color:#859076;margin:5px 0 0}.qualitative-tag{margin-left:auto;align-self:flex-start;font-size:10px;white-space:nowrap;padding:4px 8px;border-radius:20px;border:1px solid #d3dfc8;color:#718563;background:#ffffff6b}.nutrient-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.nutrient-card{border:1px solid #e2e7dc;border-radius:15px;background:#fff;padding:17px;display:flex;flex-direction:column;min-width:0}.nutrient-title{display:flex;align-items:center;gap:8px}.nutrient-title h3{font-size:13px;font-weight:600;color:#41543a;margin:0}.nutrient-symbol{background:#ecf2e5;color:#718766;width:28px;height:28px;display:grid;place-items:center;border-radius:9px;font-family:Georgia,serif;font-size:14px;font-style:italic}.tone-wheat .nutrient-symbol{background:#f6f0de;color:#a89454}.tone-peach .nutrient-symbol{background:#f8eade;color:#b18b69}.tone-olive .nutrient-symbol{background:#f1f3dd;color:#939759}.source-label{font-size:10px;letter-spacing:.02em;color:#9aa18d;margin:14px 0 8px}.source-chips{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:14px}.source-chips span{font-size:11px;padding:3px 7px;background:#f5f7f0;color:#687959;border-radius:4px;overflow-wrap:anywhere}.missing-source{font-size:12px;color:#8a957d;margin:0 0 14px;line-height:1.6}.amount-note{display:flex;justify-content:space-between;gap:8px;border-top:1px solid #edf0e7;margin:auto 0 0;padding-top:11px;font-size:10px;color:#96a087}.amount-note strong{font-weight:400;color:#818e73}.nutrition-note{font-size:11px;line-height:1.8;color:#85917b;margin:15px 0 24px}.explanation-block{margin-top:25px}.explanation-block>h3,.risk-section h3{display:flex;align-items:center;gap:7px;font-size:13px;font-weight:600;margin:0 0 13px;color:#49613d}.section-dot{height:6px;width:6px;border-radius:50%;background:#91ac7b}.explanation-list{font-size:12px;color:#6b7a5e;line-height:1.85;padding-left:18px;margin:0}.explanation-list li+li{margin-top:7px}.goal-card{background:#fff;border:1px solid #e4e9dd;border-radius:13px;padding:15px;margin-top:10px}.goal-heading{display:flex;align-items:center;gap:12px;justify-content:space-between;margin-bottom:10px}.goal-heading h4{font-size:13px;margin:0;color:#546747}.match-status{font-size:10px;padding:3px 7px;border-radius:5px;white-space:nowrap;background:#eff2e8;color:#79866b}.match-preference_match{background:#edf4e5;color:#67884f}.match-caution{background:#faf1df;color:#a08552}.goal-facts{font-size:11px;line-height:1.8;color:#839077;margin:9px 0 0}.goal-limitation{font-size:11px;line-height:1.8;color:#8a927f;border-top:1px solid #eef0e9;padding-top:9px;margin:10px 0 0}.reference-links{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap;font-size:10px;line-height:1.8;margin-top:9px;color:#959d8a}.reference-links a{color:#69875c;text-underline-offset:3px;overflow-wrap:anywhere}.risk-section{padding:17px 18px;background:#fbf5e9;border:1px solid #eee3cd;border-radius:13px;margin:22px 0;color:#937641}.risk-section h3{color:#967b45}.risk-section h3 svg{height:19px;width:19px}.risk-section .explanation-list{color:#927c53}.risk-ingredients{display:block;font-size:10px;color:#ab9570;margin-top:4px}.nutrition-details{font-size:12px;color:#6e805f;border-top:1px solid #e3e8db;padding:16px 0}.nutrition-details summary{cursor:pointer;line-height:1.8}.nutrition-details summary>span{float:right;font-size:10px;color:#919e83}.contribution-list{padding-top:4px}.contribution-list article{padding:13px 0;border-bottom:1px dashed #e7eadf}.contribution-list article:last-child{border-bottom:0}.contribution-list article>div{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}.contribution-list strong{font-weight:600}.contribution-list article>div span{font-size:10px;color:#8b9b7c}.contribution-list p{font-size:11px;line-height:1.8;margin:7px 0;color:#7d8c6c}.contribution-list small{font-size:10px;color:#9ba68d;word-break:break-all}.limitations{padding-top:14px}.nutrition-empty{text-align:center;padding:55px 25px;color:#718467}.empty-leaf{font-size:62px;color:#aabc96}.nutrition-empty h2{font-size:19px;font-weight:500;margin:16px 0 12px}.nutrition-empty p{max-width:340px;margin:0 auto;color:#8a9780;font-size:12px;line-height:1.9}.nutrition-empty>span:last-child{display:block;font-size:10px;line-height:1.8;color:#9da88f;margin-top:25px}.nutrition-details summary:focus-visible{outline:2px solid #739665;outline-offset:3px}
@media(max-width:520px){.nutrition-heading h2{font-size:20px}.nutrient-grid{gap:9px}.nutrient-card{padding:13px}.energy-card{padding:17px;gap:12px}.qualitative-tag{position:absolute;right:12px;top:12px}.source-chips span{font-size:10px}.nutrition-details summary>span{float:none;margin-left:10px}}
</style>
