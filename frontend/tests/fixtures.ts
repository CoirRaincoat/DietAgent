/**
 * Handwritten SYNTHETIC UI fixtures. No original profile, dialogue or recipe
 * file is read. Mocked UI checks do not establish recipe authenticity, backend
 * rule correctness, DeepSeek availability, or model-understanding accuracy.
 */
import type { Page, Route } from '@playwright/test'

export const SESSION_A = 'a'.repeat(32)
export const SESSION_B = 'b'.repeat(32)
export const SESSION_C = 'c'.repeat(32)

export interface RequestPayload {
  user_id: number
  message: string
  request_id: string
  session_id?: string
}

type Field = 'people' | 'meal_type' | 'restrictions' | 'allergy'

export const profiles = {
  data_scope: 'synthetic',
  profiles: [
    { user_id: 900001, label: '合成画像 900001', allergies: [], health_goals: [], preferences: [] },
    { user_id: 900002, label: '合成画像 900002', allergies: ['海鲜', '花生'], health_goals: ['控糖'], preferences: ['清淡'] },
    { user_id: 900003, label: '合成画像 900003', allergies: [], health_goals: ['降压', '护心'], preferences: ['清淡'] },
  ],
}

const questions: Record<Field, { field: Field; prompt: string; options: string[] }> = {
  people: { field: 'people', prompt: '这餐几个人吃？', options: ['1人', '2人', '3人', '4人'] },
  meal_type: { field: 'meal_type', prompt: '安排哪一餐？', options: ['早餐', '午餐', '晚餐'] },
  restrictions: { field: 'restrictions', prompt: '有什么过敏食材或忌口？没有也请说明。', options: ['没有其他忌口', '按档案忌口', '补充忌口食材'] },
  allergy: { field: 'allergy', prompt: '请明确具体过敏食材。', options: [] },
}

export const INSUFFICIENT_MESSAGE = '数据不足，暂无法准确评估热量与营养摄入量。'

export function dish(slot: number, name: string, identity = slot) {
  const recipeId = `synthetic_ui_recipe_${identity}`
  const ingredientName = identity === 2 ? '豆腐' : identity === 4 ? '鸡蛋' : '南瓜'
  const steps = `1. 处理${ingredientName}。\n2. 放入蒸锅蒸熟。`
  const nutrition = {
    recipe_id: recipeId,
    source_row: identity + 1,
    protein_sources: identity === 2 || identity === 4 ? [ingredientName] : [],
    carbohydrate_sources: ingredientName === '南瓜' ? ['南瓜'] : [],
    fat_sources: [],
    dietary_fiber: ingredientName === '南瓜' ? ['南瓜'] : [],
    ingredient_contributions: [{
      recipe_id: recipeId, source_row: identity + 1, ingredient_name: ingredientName,
      roles: identity === 2 || identity === 4 ? ['protein'] : ['carbohydrate', 'dietary_fiber'],
      explanation: '合成测试配料的定性贡献，不表示含量或健康效果。', quantity_recorded: false,
    }],
    goal_matches: [{
      goal: '护心', status: 'insufficient_data', ingredient_names: [], methods: [],
      reasons: ['当前没有这一目标的可验证营养数据。'], limitation: INSUFFICIENT_MESSAGE, sources: [],
    }],
    suitable_reasons: ['合成测试菜单的食材构成说明。'],
    risks: [{ code: 'missing_quantity', message: INSUFFICIENT_MESSAGE, ingredient_names: [ingredientName], recipe_ids: [recipeId] }],
    limitations: [INSUFFICIENT_MESSAGE], analysis_type: 'qualitative',
  }
  return {
    slot, recipe_id: recipeId, name, ingredients: [ingredientName], steps,
    reasons: ['依据已确认的用餐信息展示这一合成测试菜品。'],
    nutrition_notes: [INSUFFICIENT_MESSAGE], source: 'UI 测试合成菜谱（非真实推荐）',
    card: { title: name, subtitle: '合成测试菜品', badges: ['蒸'], image_url: null, cooking_minutes: null, servings: null },
    ingredient_details: [{ raw: `${ingredientName}适量`, name: ingredientName, quantity: null, unit: null }],
    cooking_steps: [{ number: 1, description: `处理${ingredientName}。` }, { number: 2, description: '放入蒸锅蒸熟。' }],
    provenance: { recipe_id: recipeId, source_row: identity + 1, fingerprint: String(identity).repeat(64) },
    nutrition, replacement_reason: null as string | null,
  }
}

export function initialMenu() {
  return [dish(1, '清蒸南瓜测试菜'), dish(2, '香菇豆腐测试菜'), dish(3, '蔬菜拼盘测试菜')]
}

type Dish = ReturnType<typeof dish>

function state(revision: number, session = SESSION_A, userId = 900001) {
  return {
    session_id: session, user_id: userId, revision,
    constraints: {
      allergies: userId === 900002 ? ['海鲜', '花生'] : [], excluded_ingredients: [],
      preferred_ingredients: [], inventory: null, preferences: [], health_goals: [], no_spicy: false,
      meal_type: '晚餐', dish_count: 3, soup_count: 0, people: 2, max_minutes: null,
    },
    menu_ids: [] as string[], menu_valid: false, pending_allergy: false, pending_allergy_terms: [] as string[],
    pending_clarification: null as string | null, last_message: '', history: [],
    confirmed_fields: [] as Field[], pending_fields: [] as Field[],
  }
}

export function clarification(
  fields: Field[] = ['people', 'meal_type', 'restrictions'],
  revision = 1, session = SESSION_A, userId = 900001,
) {
  const conversationState = state(revision, session, userId)
  conversationState.pending_fields = fields
  conversationState.confirmed_fields = (['people', 'meal_type', 'restrictions'] as Field[]).filter(field => !fields.includes(field))
  conversationState.pending_clarification = '请先补充这餐的用餐信息。'
  return {
    schema_version: '2.0', status: 'clarification_required', menu: [],
    reason: '请先补充这餐的用餐信息。', constraints: ['合成测试会话'],
    conversation_state: conversationState, replacement_suggestions: [], warnings: [],
    tool_calls: [], timings_ms: { total: 1 }, explanation_source: 'verified_template',
    clarification_questions: fields.map(field => questions[field]), nutrition_analysis: null,
  }
}

export function menuResponse(
  revision = 1, menu: Dish[] = initialMenu(), session = SESSION_A, userId = 900001,
) {
  const conversationState = state(revision, session, userId)
  conversationState.menu_ids = menu.map(item => item.recipe_id)
  conversationState.menu_valid = true
  conversationState.confirmed_fields = ['people', 'meal_type', 'restrictions']
  const analyses = menu.map(item => item.nutrition)
  const suggestion = dish(1, '替换候选测试菜', 5)
  suggestion.replacement_reason = '同一位置的合成测试候选；选择后仍需要后端规划。'
  return {
    schema_version: '2.0', status: 'ok', menu,
    reason: '菜单依据已确认信息展示；营养仅作食材组成的定性说明。',
    constraints: ['2 人，晚餐，共 3 道'], conversation_state: conversationState,
    replacement_suggestions: [suggestion], warnings: [INSUFFICIENT_MESSAGE],
    tool_calls: ['recipe_search', 'health_check', 'menu_modify', 'nutrition_analysis'].map(name => ({ name, summary: { count: 3 } })),
    timings_ms: { total: 2 }, explanation_source: 'verified_template', clarification_questions: [],
    nutrition_analysis: {
      recipe_ids: conversationState.menu_ids,
      protein_sources: ['豆腐'], carbohydrate_sources: ['南瓜'], fat_sources: [], dietary_fiber: ['南瓜'],
      ingredient_contributions: analyses.flatMap(analysis => analysis.ingredient_contributions),
      goal_matches: analyses[0].goal_matches, suitable_reasons: ['合成测试菜单的食物来源构成。'],
      risks: analyses[0].risks, limitations: [INSUFFICIENT_MESSAGE], recipe_analyses: analyses,
      analysis_type: 'qualitative',
    },
  }
}

export interface MockStep {
  body?: unknown
  status?: number
  networkError?: boolean
  waitUntil?: Promise<unknown>
}

export async function mockAPI(page: Page, steps: MockStep[] = []) {
  const requests: RequestPayload[] = []
  const pending = [...steps]
  const unexpected: string[] = []
  const fulfill = (route: Route, body: unknown, status = 200) => route.fulfill({
    status, contentType: 'application/json; charset=utf-8', body: JSON.stringify(body),
  })
  await page.route(/\/(?:api\/)?health(?:\?.*)?$/, route => fulfill(route, {
    status: 'ok', recipe_count: 2000, profile_count: 3, profile_data_scope: ['synthetic'],
    llm_configured: true, model: 'ui-mock-no-model',
    tools: ['recipe_search', 'health_check', 'menu_modify', 'nutrition_analysis'],
  }))
  await page.route(/\/(?:api\/)?demo\/profiles(?:\?.*)?$/, route => fulfill(route, profiles))
  await page.route(/\/(?:api\/)?chat(?:\?.*)?$/, async route => {
    if (route.request().method() !== 'POST') return route.continue()
    requests.push(route.request().postDataJSON() as RequestPayload)
    const step = pending.shift()
    if (!step) {
      unexpected.push('Unexpected extra chat request')
      return fulfill(route, { detail: { code: 'MOCK_EXHAUSTED', message: '合成 UI 测试响应已耗尽。' } }, 500)
    }
    if (step.waitUntil) await step.waitUntil
    if (step.networkError) return route.abort('failed')
    return fulfill(route, step.body, step.status ?? 200)
  })
  return { requests, unexpected, remaining: () => pending.length }
}

export const unavailable = {
  status: 503,
  body: { detail: { code: 'LLM_UNAVAILABLE', message: '模型服务暂不可用，请稍后重试。' } },
}
