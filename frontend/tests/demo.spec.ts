import { expect, test } from '@playwright/test'
import { SESSION_A, clarification, dish, initialMenu, menuResponse, mockAPI } from './fixtures'

test.describe('/demo：三个明确标记的合成 HTTP mock 场景', () => {
  test('Case1减脂：主动澄清后展示菜单与营养边界', async ({ page }) => {
    const api = await mockAPI(page, [
      { body: clarification(['people', 'restrictions']) },
      { body: clarification(['restrictions'], 2) },
      { body: menuResponse(3) },
    ])
    await page.goto('/demo')
    for (const caseId of [1, 2, 3]) await expect(page.getByTestId(`demo-case-${caseId}`)).toBeVisible()
    await page.getByTestId('demo-case-1').click()
    await page.getByTestId('clarification-people').last().getByRole('button', { name: '2人', exact: true }).click()
    await page.getByTestId('clarification-restrictions').last().getByRole('button', { name: '没有其他忌口', exact: true }).click()
    await expect(page.getByTestId('menu-card-1')).toContainText('清蒸南瓜测试菜')
    await page.getByRole('tab', { name: '营养分析', exact: true }).click()
    await expect(page.getByTestId('nutrition-summary')).toContainText('数据不足')
    expect(api.requests[0].user_id).toBe(900001)
    expect(api.requests[0].session_id).toBeUndefined()
    expect(api.requests[0].message).toBe('最近想减脂，帮我安排晚餐')
    expect(api.requests[1].session_id).toBe(SESSION_A)
    expect(api.remaining()).toBe(0)
    expect(api.unexpected).toEqual([])
  })

  test('Case2换菜：案例引导发送排除鱼的后续约束', async ({ page }) => {
    const fishMenu = initialMenu()
    fishMenu[1] = dish(2, '清蒸鱼合成测试菜', 6)
    const changedMenu = initialMenu()
    changedMenu[1] = dish(2, '蒸蛋替换合成测试菜', 4)
    const api = await mockAPI(page, [{ body: menuResponse(1, fishMenu) }, { body: menuResponse(2, changedMenu) }])
    await page.goto('/demo')
    await page.getByTestId('demo-case-2').click()
    await expect(page.getByTestId('menu-card-2')).toContainText('清蒸鱼合成测试菜')
    await page.getByTestId('demo-next-step').click()
    await expect(page.getByTestId('menu-card-2')).toContainText('蒸蛋替换合成测试菜')
    expect(api.requests[0].user_id).toBe(900001)
    expect(api.requests[0].session_id).toBeUndefined()
    expect(api.requests[0].message).toBe('2人晚餐，没有其他忌口，三道菜，不要汤，想吃鱼。')
    expect(api.requests[1].message).toBe('本餐排除鱼，请重新规划整份菜单，保留用餐人数、餐次和其他要求。')
    expect(api.requests[1].session_id).toBe(SESSION_A)
    expect(api.unexpected).toEqual([])
  })

  test('Case3忌口：案例引导继续当前会话，不伪造筛选动画', async ({ page }) => {
    const changed = menuResponse(2)
    changed.conversation_state.constraints.no_spicy = true
    changed.constraints.push('已确认：不吃辣')
    changed.reason = '已按不吃辣要求筛选；当前展示为合成 UI 验收响应。'
    const api = await mockAPI(page, [{ body: menuResponse() }, { body: changed }])
    await page.goto('/demo')
    await page.getByTestId('demo-case-3').click()
    await expect(page.getByTestId('menu-card-1')).toBeVisible()
    await page.getByTestId('demo-next-step').click()
    await expect(page.getByText(changed.reason, { exact: true })).toBeVisible()
    expect(api.requests[0].user_id).toBe(900001)
    expect(api.requests[0].session_id).toBeUndefined()
    expect(api.requests[0].message).toBe('2人晚餐，没有其他忌口，三道菜，不要汤。')
    expect(api.requests[1].message).toBe('不能吃辣')
    expect(api.requests[1].session_id).toBe(SESSION_A)
    expect(api.remaining()).toBe(0)
    expect(api.unexpected).toEqual([])
  })

  test('Case1未知过敏只走真实澄清，不自动发送固定人数覆盖3人', async ({ page }) => {
    const afterPeople = clarification(['restrictions'], 2)
    afterPeople.conversation_state.constraints.people = 3
    afterPeople.constraints = ['3 人，晚餐；等待忌口确认']
    const allergyQuestion = clarification(['allergy'], 3)
    allergyQuestion.conversation_state.constraints.people = 3
    allergyQuestion.conversation_state.pending_allergy = true
    allergyQuestion.conversation_state.pending_allergy_terms = ['特调酱']
    allergyQuestion.constraints = ['3 人，晚餐；待确认过敏原：特调酱']
    allergyQuestion.reason = '请明确特调酱中的具体过敏食材，确认前不生成菜单。'
    const resolved = menuResponse(4)
    resolved.conversation_state.constraints.people = 3
    resolved.conversation_state.constraints.allergies = ['花生']
    resolved.constraints = ['3 人，晚餐，共 3 道；已知花生过敏']
    const api = await mockAPI(page, [
      { body: clarification(['people', 'restrictions']) },
      { body: afterPeople }, { body: allergyQuestion }, { body: resolved },
    ])
    await page.goto('/demo')
    await page.getByTestId('demo-case-1').click()
    await expect(page.getByTestId('demo-next-step')).not.toBeVisible()
    await page.getByTestId('clarification-people').last().getByRole('button', { name: '3人', exact: true }).click()
    await expect(page.getByTestId('clarification-restrictions').last()).toBeVisible()
    await page.getByTestId('chat-input').fill('我对一种特调酱过敏。')
    await page.getByTestId('send-message').click()
    await expect(page.getByTestId('clarification-allergy').last()).toBeVisible()
    await expect(page.getByTestId('demo-next-step')).not.toBeVisible()
    await expect(page.getByText('请先回答下方澄清问题', { exact: true })).toBeVisible()
    await expect(page.getByTestId('menu-card-1')).not.toBeVisible()
    await page.getByText('已记录的偏好与执行记录', { exact: true }).click()
    await expect(page.getByText('3 人，晚餐；待确认过敏原：特调酱', { exact: true })).toBeVisible()
    expect(api.requests).toHaveLength(3)
    expect(api.remaining()).toBe(1)
    expect(api.requests.map(request => request.message)).toEqual([
      '最近想减脂，帮我安排晚餐', '3人', '我对一种特调酱过敏。',
    ])
    await page.getByTestId('chat-input').fill('特调酱里是花生，我对花生过敏。')
    await page.getByTestId('send-message').click()
    await expect(page.getByTestId('menu-card-1')).toBeVisible()
    await expect(page.getByText('3 人 · 晚餐 · 3 道菜', { exact: true })).toBeVisible()
    expect(api.requests).toHaveLength(4)
    expect(api.requests.slice(1).every(request => request.session_id === SESSION_A)).toBe(true)
    expect(new Set(api.requests.map(request => request.request_id)).size).toBe(4)
    expect(api.requests.some(request => /2人|2个人/.test(request.message))).toBe(false)
    expect(api.unexpected).toEqual([])
  })

  for (const status of ['clarification_required', 'no_feasible_menu'] as const) {
    test(`Case2后续返回${status}时不虚报步骤完成或保留旧菜单`, async ({ page }) => {
      const unresolved = clarification(status === 'clarification_required' ? ['restrictions'] : [], 2)
      unresolved.status = status
      unresolved.reason = status === 'clarification_required'
        ? '替换前仍需补充具体食材要求。' : '当前条件下未找到可行菜单，请调整本餐需求。'
      unresolved.conversation_state.menu_ids = initialMenu().map(item => item.recipe_id)
      unresolved.conversation_state.menu_valid = false
      const api = await mockAPI(page, [{ body: menuResponse() }, { body: unresolved }])
      await page.goto('/demo')
      await page.getByTestId('demo-case-2').click()
      await expect(page.getByTestId('menu-card-1')).toBeVisible()
      await page.getByTestId('demo-next-step').click()
      await expect(page.getByText(unresolved.reason, { exact: true })).toBeVisible()
      await expect(page.getByText('这一步已完成', { exact: true })).not.toBeVisible()
      await expect(page.getByTestId('demo-next-step')).not.toBeVisible()
      await expect(page.getByTestId('menu-card-1')).not.toBeVisible()
      await expect(page.getByText(status === 'clarification_required'
        ? '请先回答下方澄清问题' : '请先调整本餐需求', { exact: true })).toBeVisible()
      expect(api.requests).toHaveLength(2)
      expect(api.requests[1].message).toBe('本餐排除鱼，请重新规划整份菜单，保留用餐人数、餐次和其他要求。')
      expect(api.requests[1].session_id).toBe(SESSION_A)
      expect(api.unexpected).toEqual([])
    })
  }
})
