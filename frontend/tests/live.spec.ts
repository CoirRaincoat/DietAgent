/**
 * Opt-in real service check. Only synthetic profile 900001 and the five fixed,
 * authored demo messages are sent. No original profiles/dialogues are loaded.
 * Passing is integration evidence, not an NLU accuracy or clinical evaluation.
 */
import { expect, test, type Page } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

function artifact(name: string) {
  return fileURLToPath(new URL(`../../artifacts/${name}`, import.meta.url))
}

function saveReport(name: string, report: unknown) {
  const path = artifact(name)
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, JSON.stringify(report, null, 2), 'utf-8')
}

async function screenshot(page: Page, name: string) {
  await page.screenshot({ path: artifact(`screenshots/${name}.png`), fullPage: true })
}

function observation(body: any, turn: number, httpStatus: number, started: number) {
  return {
    turn, http_status: httpStatus, status: body.status,
    session_id: body.conversation_state?.session_id,
    revision: body.conversation_state?.revision,
    menu_ids: (body.menu || []).map((item: { recipe_id: string }) => item.recipe_id),
    menu_names: (body.menu || []).map((item: { name: string }) => item.name),
    people: body.conversation_state?.constraints.people,
    meal_type: body.conversation_state?.constraints.meal_type,
    no_spicy: body.conversation_state?.constraints.no_spicy,
    health_goals: body.conversation_state?.constraints.health_goals,
    excluded_ingredients: body.conversation_state?.constraints.excluded_ingredients,
    pending_fields: body.conversation_state?.pending_fields,
    reason: body.reason,
    tool_names: (body.tool_calls || []).map((event: { name: string }) => event.name),
    explanation_source: body.explanation_source,
    elapsed_ms: Date.now() - started,
  }
}

test.describe('固定合成资料真实浏览器集成', () => {
  test.describe.configure({ mode: 'default' })
  test.skip(process.env.E2E_LIVE !== '1', 'Set E2E_LIVE=1 explicitly to call the configured real model.')

  test('@live 澄清、菜单、指定换菜、解释保留菜单', async ({ page }) => {
    test.setTimeout(150_000)
    const payloads: { user_id: number; message: string; session_id?: string; request_id: string }[] = []
    const observations: ReturnType<typeof observation>[] = []
    const startedAt = Date.now()
    const persist = (passed: boolean) => saveReport('phase10-live-five-turn.json', {
      data_scope: 'synthetic', user_id: 900001, base_url: test.info().project.use.baseURL,
      started_at: new Date(startedAt).toISOString(), passed,
      elapsed_ms: Date.now() - startedAt, turns: observations, requests: payloads,
    })
    persist(false)
    page.on('request', request => {
      if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/chat')) {
        payloads.push(request.postDataJSON())
      }
    })

    async function nextTurn(action: () => Promise<unknown>) {
      const started = Date.now()
      const [response] = await Promise.all([
        page.waitForResponse(response => response.request().method() === 'POST'
          && new URL(response.url()).pathname.endsWith('/chat'), { timeout: 90_000 }),
        action(),
      ])
      expect(response.status()).toBe(200)
      const body = await response.json()
      observations.push(observation(body, observations.length + 1, response.status(), started))
      persist(false)
      return body
    }

    async function sendMessage(target: Page, message: string) {
      await target.getByTestId('chat-input').fill(message)
      await target.getByTestId('send-message').click()
    }

    await page.goto('/')
    await expect(page.getByTestId('profile-select')).toHaveValue('900001')
    await screenshot(page, 'phase10-live-initial-desktop')
    const first = await nextTurn(() => sendMessage(page, '帮我安排一餐。'))
    expect(first.status).toBe('clarification_required')
    expect(first.menu).toEqual([])
    expect(first.tool_calls).toEqual([])
    await expect(page.getByTestId('clarification-people').last()).toBeVisible()

    const second = await nextTurn(() => sendMessage(page, '2个人，晚餐。'))
    expect(second.status).toBe('clarification_required')
    await expect(page.getByTestId('clarification-restrictions').last()).toBeVisible()

    const third = await nextTurn(() => sendMessage(page, '没有其他忌口，不吃辣椒，安排三道菜。'))
    expect(third.status).toBe('ok')
    expect(third.menu).toHaveLength(3)
    expect(third.nutrition_analysis.analysis_type).toBe('qualitative')
    for (const item of third.menu) {
      await expect(page.getByTestId(`menu-card-${item.slot}`)).toContainText(item.name)
      expect(item.provenance.recipe_id).toBe(item.recipe_id)
    }
    await page.getByRole('tab', { name: '营养分析', exact: true }).click()
    await expect(page.getByTestId('nutrition-summary')).toContainText('数据不足')
    await screenshot(page, 'phase10-live-five-turn-nutrition')
    await page.getByRole('tab', { name: /^本餐菜单/ }).click()
    const originalIds = third.menu.map((item: { recipe_id: string }) => item.recipe_id)

    const fourth = await nextTurn(() => page.getByTestId('replace-dish-2').click())
    expect(fourth.status).toBe('ok')
    const replacedIds = fourth.menu.map((item: { recipe_id: string }) => item.recipe_id)
    expect(replacedIds[0]).toBe(originalIds[0])
    expect(replacedIds[2]).toBe(originalIds[2])
    expect(replacedIds[1]).not.toBe(originalIds[1])

    const fifth = await nextTurn(() => sendMessage(page, '解释刚才这份菜单。'))
    expect(fifth.status).toBe('ok')
    expect(fifth.menu.map((item: { recipe_id: string }) => item.recipe_id)).toEqual(replacedIds)
    expect(fifth.tool_calls.map((event: { name: string }) => event.name)).not.toContain('menu_modify')
    expect(payloads).toHaveLength(5)
    expect(payloads.every(payload => payload.user_id === 900001)).toBe(true)
    expect(payloads[0].session_id).toBeUndefined()
    expect(new Set(payloads.slice(1).map(payload => payload.session_id))).toEqual(new Set([first.conversation_state.session_id]))
    expect(new Set(payloads.map(payload => payload.request_id)).size).toBe(5)
    await test.info().attach('synthetic-live-summary', {
      body: JSON.stringify({ data_scope: 'synthetic', user_id: 900001, turns: observations }, null, 2),
      contentType: 'application/json',
    })
    await screenshot(page, 'phase10-live-five-turn-menu')
    persist(true)
  })

  test('@live /demo合成场景各自新建会话并完成对应调整', async ({ page }) => {
    test.setTimeout(180_000)
    const diagnosticCase = process.env.E2E_DIAGNOSTIC_DEMO_CASE
    if (diagnosticCase && !/^[123]$/.test(diagnosticCase)) throw new Error('Diagnostic case must be 1, 2 or 3')
    const caseIds = diagnosticCase ? [Number(diagnosticCase)] : [1, 2, 3]
    const reportName = diagnosticCase
      ? `phase10-live-demo-case${diagnosticCase}-diagnostic.json` : 'phase10-live-demo-cases.json'
    const startedAt = Date.now()
    const requests: { user_id: number; message: string; session_id?: string; request_id: string }[] = []
    const cases: { case_id: number; passed: boolean; assertion_failures: string[]; turns: ReturnType<typeof observation>[] }[] = []
    const persist = (passed: boolean) => saveReport(reportName, {
      data_scope: 'synthetic', user_id: 900001, base_url: test.info().project.use.baseURL,
      diagnostic_only: Boolean(diagnosticCase), requested_cases: caseIds,
      started_at: new Date(startedAt).toISOString(), passed, elapsed_ms: Date.now() - startedAt,
      cases, requests,
    })
    persist(false)
    page.on('request', request => {
      if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/chat')) {
        requests.push(request.postDataJSON())
      }
    })
    await page.goto('/demo')
    await expect(page.getByTestId('profile-select')).toHaveValue('900001')
    const sessions: string[] = []
    for (const caseId of caseIds) {
      const caseErrorCount = test.info().errors.length
      const entry = { case_id: caseId, passed: false, assertion_failures: [] as string[], turns: [] as ReturnType<typeof observation>[] }
      cases.push(entry)
      const requestStart = requests.length
      async function next(action: () => Promise<unknown>) {
        const started = Date.now()
        const [response] = await Promise.all([
          page.waitForResponse(response => response.request().method() === 'POST'
            && new URL(response.url()).pathname.endsWith('/chat'), { timeout: 90_000 }), action(),
        ])
        expect(response.status()).toBe(200)
        const body = await response.json()
        entry.turns.push(observation(body, entry.turns.length + 1, response.status(), started))
        persist(false)
        return body
      }
      let result = await next(() => page.getByTestId(`demo-case-${caseId}`).click())
      const session = result.conversation_state.session_id as string
      expect(sessions).not.toContain(session)
      sessions.push(session)
      expect(requests[requestStart].user_id).toBe(900001)
      expect(requests[requestStart].session_id).toBeUndefined()
      if (caseId === 1) {
        expect(result.status).toBe('clarification_required')
        const answers: Record<string, string> = { people: '2人', meal_type: '晚餐', restrictions: '没有其他忌口' }
        const answered = new Set<string>()
        for (let step = 0; step < 3 && result.status === 'clarification_required'; step += 1) {
          const field = result.clarification_questions[0]?.field as string
          expect(Object.keys(answers)).toContain(field)
          expect(answered.has(field)).toBe(false)
          answered.add(field)
          result = await next(() => page.getByTestId(`clarification-${field}`).last()
            .getByRole('button', { name: answers[field], exact: true }).click())
        }
        expect(result.status).toBe('ok')
        expect(result.conversation_state.constraints.health_goals).toContain('减脂')
        expect(result.conversation_state.constraints.people).toBe(2)
        expect(result.conversation_state.constraints.meal_type).toBe('晚餐')
        await expect(page.getByTestId('menu-card-1')).toBeVisible()
        await page.getByRole('tab', { name: '营养分析', exact: true }).click()
        await expect(page.getByTestId('nutrition-summary')).toContainText('数据不足')
        await screenshot(page, 'phase10-live-demo-case1-nutrition')
      } else {
        expect(result.status).toBe('ok')
        const priorIds = result.menu.map((item: { recipe_id: string }) => item.recipe_id)
        const previousPeople = result.conversation_state.constraints.people
        const previousMeal = result.conversation_state.constraints.meal_type
        await expect(page.getByTestId('menu-card-1')).toBeVisible()
        result = await next(() => page.getByTestId('demo-next-step').click())
        expect(result.status).toBe('ok')
        expect(result.conversation_state.constraints.people).toBe(previousPeople)
        expect(result.conversation_state.constraints.meal_type).toBe(previousMeal)
        if (caseId === 2) {
          expect(result.conversation_state.constraints.excluded_ingredients).toContain('鱼')
          expect(result.menu.map((item: { recipe_id: string }) => item.recipe_id)).not.toEqual(priorIds)
          expect(requests.at(-1)?.message).toBe('本餐排除鱼，请重新规划整份菜单，保留用餐人数、餐次和其他要求。')
        } else {
          expect(result.conversation_state.constraints.no_spicy).toBe(true)
          expect(requests.at(-1)?.message).toBe('不能吃辣')
        }
        await expect(page.getByText('这一步已完成', { exact: true })).toBeVisible()
        await screenshot(page, `phase10-live-demo-case${caseId}-menu`)
      }
      expect(result.menu).toHaveLength(3)
      expect(requests.slice(requestStart).every(request => request.user_id === 900001)).toBe(true)
      expect(requests.slice(requestStart + 1).every(request => request.session_id === session)).toBe(true)
      expect(result.conversation_state.session_id).toBe(session)
      entry.assertion_failures = test.info().errors.slice(caseErrorCount).map(error => error.message || 'Assertion failed')
      entry.passed = entry.assertion_failures.length === 0
      persist(false)
    }
    expect(new Set(requests.map(request => request.request_id)).size).toBe(requests.length)
    expect(new Set(sessions).size).toBe(caseIds.length)
    persist(cases.every(entry => entry.passed))
    await test.info().attach('synthetic-live-demo-cases', {
      body: JSON.stringify({ data_scope: 'synthetic', cases }, null, 2), contentType: 'application/json',
    })
  })
})
