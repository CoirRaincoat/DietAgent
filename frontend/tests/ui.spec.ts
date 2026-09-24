import { expect, test, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import {
  SESSION_A, SESSION_B, SESSION_C, clarification, dish, initialMenu,
  menuResponse, mockAPI, unavailable,
} from './fixtures'

async function send(page: Page, message: string) {
  await page.getByTestId('chat-input').fill(message)
  await page.getByTestId('send-message').click()
}

async function expectMenu(page: Page, names = initialMenu().map(item => item.name)) {
  for (let index = 0; index < names.length; index += 1) {
    await expect(page.getByTestId(`menu-card-${index + 1}`)).toContainText(names[index])
  }
}

async function screenshot(page: Page, name: string) {
  if (process.env.E2E_SCREENSHOTS !== '1') return
  await page.screenshot({
    path: fileURLToPath(new URL(`../../artifacts/screenshots/${name}.png`, import.meta.url)),
    fullPage: true, animations: 'disabled',
  })
}

test.describe('合成 HTTP mock：UI 与协议行为，不测真实模型', () => {
  test('首页加载合成画像，空消息不能发送', async ({ page }) => {
    const api = await mockAPI(page)
    const pageErrors: string[] = []
    page.on('pageerror', error => pageErrors.push(error.message))
    await page.goto('/')
    await expect(page.getByTestId('chat-input')).toBeVisible()
    await expect(page.getByTestId('profile-select')).toBeVisible()
    await expect(page.getByTestId('profile-select').locator('option')).toHaveCount(3)
    await expect(page.getByTestId('send-message')).toBeDisabled()
    await screenshot(page, 'phase10-initial-desktop')
    await page.getByTestId('chat-input').fill('   ')
    await expect(page.getByTestId('send-message')).toBeDisabled()
    expect(api.requests).toHaveLength(0)
    expect(pageErrors).toEqual([])
  })

  test('澄清按钮连续补齐，后续请求继承会话并使用新request_id', async ({ page }) => {
    const api = await mockAPI(page, [
      { body: clarification() },
      { body: clarification(['meal_type', 'restrictions'], 2) },
      { body: clarification(['restrictions'], 3) },
      { body: menuResponse(4) },
    ])
    await page.goto('/')
    await send(page, '帮我安排一餐。')
    await expect(page.getByTestId('clarification-people').last()).toBeVisible()
    await expect(page.getByTestId('menu-card-1')).not.toBeVisible()
    await page.getByTestId('clarification-people').last().getByRole('button', { name: '2人', exact: true }).click()
    await page.getByTestId('clarification-meal_type').last().getByRole('button', { name: '晚餐', exact: true }).click()
    await page.getByTestId('clarification-restrictions').last().getByRole('button', { name: '没有其他忌口', exact: true }).click()
    await expectMenu(page)
    expect(api.requests).toHaveLength(4)
    expect(api.requests[0].session_id).toBeUndefined()
    expect(api.requests[0].user_id).toBe(900001)
    expect(api.requests.slice(1).map(request => request.session_id)).toEqual([SESSION_A, SESSION_A, SESSION_A])
    expect(new Set(api.requests.map(request => request.request_id)).size).toBe(4)
    expect(api.requests.every(request => Boolean(request.request_id))).toBe(true)
    expect(api.requests[1].message).toContain('2人')
    expect(api.requests[2].message).toContain('晚餐')
    expect(api.requests[3].message).toContain('没有其他忌口')
    expect(api.unexpected).toEqual([])
  })

  test('菜单详情展示原始配料、步骤、来源；营养缺失不画虚构数字', async ({ page }) => {
    await mockAPI(page, [{ body: menuResponse() }])
    await page.goto('/')
    await send(page, '2人晚餐，没有其他忌口，三道菜。')
    await expectMenu(page)
    await screenshot(page, 'phase10-mock-menu-desktop')
    await page.getByTestId('recipe-details-2').click()
    const dialog = page.getByTestId('recipe-detail-dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog).toContainText('香菇豆腐测试菜')
    await expect(dialog).toContainText('豆腐适量')
    await expect(dialog).toContainText('放入蒸锅蒸熟。')
    await expect(dialog).toContainText('synthetic_ui_recipe_2')
    await page.keyboard.press('Escape')
    await page.getByRole('tab', { name: '营养分析', exact: true }).click()
    const nutrition = page.getByTestId('nutrition-summary')
    await expect(nutrition).toBeVisible()
    await expect(nutrition).toContainText('数据不足')
    await expect(nutrition).toContainText('蛋白质')
    await expect(nutrition).toContainText('碳水')
    await expect(nutrition).toContainText('脂肪')
    await expect(nutrition).toContainText('纤维')
    expect(await nutrition.innerText()).not.toMatch(/\d+(?:\.\d+)?\s*(?:kcal|千卡|千焦)/i)
    await screenshot(page, 'phase10-mock-nutrition-desktop')
    if (process.env.E2E_SCREENSHOTS === '1') {
      await page.setViewportSize({ width: 390, height: 844 })
      await page.getByRole('tab', { name: /^本餐菜单/ }).click()
      await screenshot(page, 'phase10-mock-menu-mobile')
    }
  })

  test('指定第二道换菜保持其他位置，发送明确槽位指令', async ({ page }) => {
    const changed = initialMenu()
    changed[1] = dish(2, '蒸蛋羹替换测试菜', 4)
    const api = await mockAPI(page, [{ body: menuResponse() }, { body: menuResponse(2, changed) }])
    await page.goto('/')
    await send(page, '2人晚餐，没有其他忌口，三道菜。')
    await expectMenu(page)
    await page.getByTestId('replace-dish-2').click()
    await expectMenu(page, changed.map(item => item.name))
    expect(api.requests[1].session_id).toBe(SESSION_A)
    expect(api.requests[1].message).toMatch(/只替换第(?:2|二)道菜/)
    expect(api.requests[1].message).toContain('其他保持不变')
    expect(api.requests[1].request_id).not.toBe(api.requests[0].request_id)
    await expect(page.getByTestId('menu-card-2')).not.toContainText('香菇豆腐测试菜')
    expect(api.unexpected).toEqual([])
  })

  test('503显式重试保留首轮完整payload；成功后才继承session', async ({ page }) => {
    const api = await mockAPI(page, [unavailable, { body: clarification() }, { body: menuResponse(2) }])
    await page.goto('/')
    await send(page, '帮我安排一餐。')
    await expect(page.getByTestId('chat-error')).toBeVisible()
    await expect(page.getByTestId('retry-request')).toBeVisible()
    expect(api.requests).toHaveLength(1)
    await page.getByTestId('retry-request').click()
    await expect(page.getByTestId('clarification-people').last()).toBeVisible()
    expect(api.requests[1]).toEqual(api.requests[0])
    expect(api.requests[1].session_id).toBeUndefined()
    await send(page, '2人晚餐，没有其他忌口。')
    await expectMenu(page)
    expect(api.requests[2].session_id).toBe(SESSION_A)
    expect(api.requests[2].request_id).not.toBe(api.requests[1].request_id)
    await expect(page.getByTestId('chat-error')).not.toBeVisible()
    expect(api.unexpected).toEqual([])
  })

  test('网络中断后重试沿用已有session及request_id', async ({ page }) => {
    const api = await mockAPI(page, [
      { body: menuResponse() }, { networkError: true }, { body: menuResponse(2) },
    ])
    await page.goto('/')
    await send(page, '2人晚餐，没有其他忌口。')
    await expectMenu(page)
    await send(page, '解释刚才的菜单。')
    await expect(page.getByTestId('chat-error')).toBeVisible()
    await page.getByTestId('retry-request').click()
    await expect(page.getByTestId('chat-error')).not.toBeVisible()
    await expect.poll(() => api.requests.length).toBe(3)
    await expect(page.getByTestId('profile-select')).toBeEnabled()
    expect(api.requests[2]).toEqual(api.requests[1])
    expect(api.requests[2].session_id).toBe(SESSION_A)
    expect(api.unexpected).toEqual([])
  })

  test('409冲突提示重新开始，避免盲目重放旧请求', async ({ page }) => {
    const api = await mockAPI(page, [
      { status: 409, body: { detail: { code: 'SESSION_CONFLICT', message: '会话版本已变化，请重新开始。' } } },
      { body: menuResponse(1, initialMenu(), SESSION_B) },
    ])
    await page.goto('/')
    await send(page, '合成会话冲突测试。')
    await expect(page.getByTestId('chat-error')).toBeVisible()
    await expect(page.getByTestId('retry-request')).not.toBeVisible()
    await page.getByTestId('new-session').click()
    await send(page, '2人晚餐，没有其他忌口。')
    await expectMenu(page)
    expect(api.requests[1].session_id).toBeUndefined()
    expect(api.requests[1].request_id).not.toBe(api.requests[0].request_id)
  })

  test('新会话清空旧菜单并省略旧session_id', async ({ page }) => {
    const api = await mockAPI(page, [
      { body: menuResponse() }, { body: clarification(['people'], 1, SESSION_B) },
    ])
    await page.goto('/')
    await send(page, '2人晚餐，没有其他忌口。')
    await expectMenu(page)
    await page.getByTestId('chat-input').fill('旧会话尚未发送的忌口草稿')
    await page.getByTestId('new-session').click()
    await expect(page.getByTestId('menu-card-1')).not.toBeVisible()
    await expect(page.getByTestId('chat-input')).toHaveValue('')
    await send(page, '新的一餐。')
    await expect(page.getByTestId('clarification-people').last()).toBeVisible()
    expect(api.requests[1].session_id).toBeUndefined()
    expect(api.requests[1].request_id).not.toBe(api.requests[0].request_id)
  })

  test('切换合成用户清空会话，切回后也不串用旧session', async ({ page }) => {
    const api = await mockAPI(page, [
      { body: menuResponse() },
      { body: menuResponse(1, initialMenu(), SESSION_B, 900002) },
      { body: clarification(['people'], 1, SESSION_C, 900001) },
    ])
    await page.goto('/')
    await send(page, '2人晚餐，没有其他忌口。')
    await expectMenu(page)
    await page.getByTestId('chat-input').fill('用户一尚未发送的忌口草稿')
    await page.getByTestId('profile-select').selectOption('900002')
    await expect(page.getByTestId('menu-card-1')).not.toBeVisible()
    await expect(page.getByTestId('chat-input')).toHaveValue('')
    await send(page, '2人晚餐，按档案忌口。')
    await expectMenu(page)
    expect(api.requests[1].user_id).toBe(900002)
    expect(api.requests[1].session_id).toBeUndefined()
    await page.getByTestId('chat-input').fill('用户二尚未发送的偏好草稿')
    await page.getByTestId('profile-select').selectOption('900001')
    await expect(page.getByTestId('menu-card-1')).not.toBeVisible()
    await expect(page.getByTestId('chat-input')).toHaveValue('')
    await send(page, '请安排一餐。')
    await expect(page.getByTestId('clarification-people').last()).toBeVisible()
    expect(api.requests[2].user_id).toBe(900001)
    expect(api.requests[2].session_id).toBeUndefined()
    expect(new Set(api.requests.map(request => request.request_id)).size).toBe(3)
  })

  test('请求执行中不能切换用户或新建会话', async ({ page }) => {
    let release!: () => void
    const waitUntil = new Promise<void>(resolve => { release = resolve })
    const api = await mockAPI(page, [{ body: menuResponse(), waitUntil }])
    await page.goto('/')
    await send(page, '2人晚餐，没有其他忌口。')
    await expect.poll(() => api.requests.length).toBe(1)
    await expect(page.getByTestId('profile-select')).toBeDisabled()
    await expect(page.getByTestId('new-session')).toBeDisabled()
    await expect(page.getByTestId('send-message')).toBeDisabled()
    release()
    await expectMenu(page)
    await expect(page.getByTestId('profile-select')).toBeEnabled()
    await expect(page.getByTestId('new-session')).toBeEnabled()
  })
})
