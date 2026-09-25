# 比赛 Demo API（v0.3.0）

默认服务仅加载 3 个手写合成画像与原始菜谱 CSV。原始健康档案和原始对话只用于本地验收；DeepSeek 适配器在发出请求前拒绝 `data_scope=original` 的画像。没有从 HTTP 传入或覆盖 data_scope 的接口。

用户界面为 `http://localhost:8080`，演示页为 `/demo`；前端请求 `/api/` 前缀，由 Nginx 转发到以下原有 API。原 `/health`、`/chat`、`/demo/profiles`、`/docs` 兼容入口继续可用。响应 schema_version 仍为 2.0。

单独调试后端（不包含前端）的启动命令：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Compose部署的接口文档：`http://localhost:8080/docs`；单独后端调试对应 `http://localhost:8000/docs`。本版本无鉴权，限本机 Demo、单 worker；`POST /chat` 返回完整 JSON，不是 SSE。

## GET /health

返回 status、recipe_count、profile_count、profile_data_scope、llm_configured、model、tools。默认应为 2000 条菜谱、3 个 synthetic 画像和四个业务工具。`llm_configured=true` 只说明存在配置，不证明远端连通。

## GET /demo/profiles

列出当前可用合成画像的 user_id、label、allergies、health_goals、preferences；不会列出注入测试目录中的原始画像。

- 900001：基础用餐与主动澄清。
- 900002：海鲜/花生过敏及控糖偏好。
- 900003：降压及暂未支持的护心目标，用于说明能力边界。

所有信息都是手写的演示资料，不对应真实个人。

## POST /chat

`Content-Type: application/json`。首轮示例：

```json
{
  "user_id": 900001,
  "message": "帮我安排一餐。",
  "request_id": "demo-1"
}
```

| 请求字段 | 约束 |
| --- | --- |
| user_id | 正整数，默认选 900001—900003 |
| message | 去除首尾空白后非空，最多 2000 字符 |
| session_id | 首轮省略，后续使用返回的 32 位小写十六进制 ID |
| request_id | 可选，1—128 字符；新操作用新 ID，重试保留原 ID 和原消息 |

拒绝额外字段。首次提供 request_id 时生成稳定会话 ID，首轮响应丢失后也可重试。相同请求且会话版本未前进时返回已完成结果；同 ID 换消息、跨用户访问或重试旧版本返回 409。尚未提供进行中请求的崩溃恢复及完整 exactly-once 保证。

### 主动澄清

人数、餐次、忌口未明确时返回 clarification_required、空 menu、null nutrition_analysis，不执行检索/规划。已有档案过敏算已知忌口；无过敏档案仍需说明本餐忌口或明确“没有其他忌口”。

以下是响应字段片段：

```json
{
  "schema_version": "2.0",
  "status": "clarification_required",
  "menu": [],
  "clarification_questions": [
    {"field": "people", "prompt": "这餐几个人吃？", "options": ["1人", "2人", "3人", "4人"]},
    {"field": "meal_type", "prompt": "安排哪一餐？", "options": ["早餐", "午餐", "晚餐"]},
    {"field": "restrictions", "prompt": "有什么过敏食材或忌口？没有也请说明。", "options": ["没有其他忌口", "按档案忌口", "补充忌口食材"]}
  ],
  "nutrition_analysis": null
}
```

继续使用同一 session_id，依次回答“2个人，晚餐。”“没有其他忌口，不吃辣椒，安排三道菜。”即可。确认字段记录于 conversation_state.confirmed_fields，待答字段记录于 pending_fields。模糊回答不能把默认值变成已确认；明确无其他忌口也不会撤销已知过敏。未知过敏词位于 pending_allergy_terms，必须澄清后才继续。

### 多人共享菜单

多人场景通过自然语言提供成员、称呼、是否参加以及归属于该人的过敏、忌口和偏好。例如：“我和爸妈三个人晚餐，我爸花生过敏，我妈不吃辣，没有其他忌口。”系统为成员保存稳定 `diner_id` 和别名；后续“爸爸今晚不参加”只改变本餐出席状态，原有个人事实仍保留。

当前规划的是一桌共享菜。任何参餐者的已知过敏、排除食材和不辣要求都会合并到 `conversation_state.constraints`，供检索、规划和最终校验使用，不能用“该成员不吃这道菜”绕过。`meal_constraints` 保存未归属到某个人的整桌约束，`diners` 保存逐人成员状态。退出本餐的成员不再贡献本轮聚合约束；重新参加时其原事实重新生效。

当用户没有明确菜数时，工程默认值为：1—2 人 3 道且无汤，3—4 人 4 道含 1 汤，5—6 人 5 道含 1 汤，7—8 人 6 道含 1 汤。用户明确总菜数或汤数后，后续人数变化不覆盖该结构。已确认总人数小于实名参餐者数量、称呼映射不唯一、或个人过敏词无法可靠映射时，接口返回 `clarification_required`，不会执行检索和规划。

成功响应新增 `diner_suitability`。每位当前参餐者分别返回已知约束、硬约束是否满足、违反项、未覆盖的软偏好及范围说明。该字段只对已提供事实做核对，不推断未提供的疾病、营养数值或健康结论；未实名的其余人数会在 `warnings` 和 `constraints` 中说明信息仍未知。

### 完整响应结构

| 字段 | 用途 |
| --- | --- |
| schema_version | 当前 2.0；旧 menu/name/ingredients/steps 等字段保留 |
| status | ok / clarification_required / no_feasible_menu |
| menu | 菜品与卡片数组，不能规划时为空 |
| reason | 程序核验事实组成的解释，模型仅选择事实 ID |
| constraints | 已确认约束及尚待确认信息的中文说明 |
| conversation_state | 会话 ID、版本、确认字段、约束、菜单有效性、rejected_recipe_ids 及有限历史 |
| diner_suitability | 当前参餐者逐人已知约束核验；不把未知信息写成已满足 |
| clarification_questions | field、prompt、options，可直接用于前端提问 |
| nutrition_analysis | 整餐定性组成、目标匹配、食材贡献与风险；无菜单时 null |
| replacement_suggestions | 适用于指定槽位的库内候选，不自动应用 |
| warnings | 未支持目标、数据不足、模型解释回退等提示 |
| tool_calls | 真实工具名及计数，不包含原始健康档案 |
| timings_ms | 本轮解析、检索规则规划、解释和总耗时；不含等待锁的时间 |
| explanation_source | deepseek_verified_facts 或 verified_template |

### 菜品卡片

menu 和 replacement_suggestions 中每项包含：

| 字段 | 用途 |
| --- | --- |
| slot / recipe_id / name | 位置与真实菜谱身份 |
| ingredients / steps | 兼容旧客户端的食材名列表和原始步骤全文 |
| ingredient_details | name、raw、quantity、unit；未知数量/单位保持 null |
| cooking_steps | 按原始换行分段的 number、description；单段原文保留为一步，不编步骤 |
| card | title、subtitle、低风险 badges；未知 image_url、cooking_minutes、servings 为 null |
| provenance | recipe_id、source_row、fingerprint，用于追溯原始 CSV |
| nutrition | 单菜来源解释与目标匹配，结构见 NUTRITION.md |
| reasons / nutrition_notes | 原有规则理由和定性句子 |
| replacement_reason | 建议项的替换理由，普通菜单项为 null |

替换建议针对 slot 指明的位置，当前生成第 1 道菜的最多 2 个同类候选，不保证其他菜品都有建议。避免与当前菜单同名；建议经过相同限制检查。当前自然语言“只换第二道菜”支持定点换菜，尚不支持直接提交某个建议 ID 强制选菜。

### 拒绝记忆与食材偏好

`conversation_state.rejected_recipe_ids` 是本餐已明确整份否定的菜品ID列表，默认 `[]`；旧会话自动兼容。该列表在规划前保存，重启或无可行菜单后仍生效，菜单和建议也排除同名变体。普通局部换菜不写入此列表；新会话重新开始，当前不支持撤销拒绝记录。

食材偏好在整份菜单层面尽量覆盖，不要求每道菜都包含所有偏好。已有菜单缺少偏好时可做局部交换；交换保留已覆盖偏好、硬约束和汤数。指定换菜时不会为补齐偏好扩大软修改范围；未覆盖的偏好在 `warnings` 中说明，不单独导致 `no_feasible_menu`。这是有界启发式，不保证全局最少修改。若缩减菜数后指定替换位置超出新总菜数，返回 `no_feasible_menu` 并解释冲突，不输出菜单。

## 五轮 Demo

在同一会话依次发送：

1. 帮我安排一餐。
2. 2个人，晚餐。
3. 没有其他忌口，不吃辣椒，安排三道菜。
4. 只替换第二道菜，其他保持不变。
5. 解释刚才这份菜单。

前两轮应澄清；第三轮生成卡片；第四轮保留第一、第三道；第五轮保留整个菜单且不调用 menu_modify。可运行 `python -m evaluation.smoke_synthetic` 自动验证，使用临时 SQLite 且只发送合成资料。

## 错误码

| HTTP | code | 含义 |
| --- | --- | --- |
| 403 | ORIGINAL_PROFILE_BLOCKED | 原始画像被模型适配器在请求前拦截 |
| 404 | NOT_FOUND | 用户或显式指定的会话不存在 |
| 409 | SESSION_CONFLICT | 会话归属、版本或重试冲突 |
| 422 | FastAPI 校验详情 | 非法字段、格式或额外字段 |
| 429 | BUSY | 进程容量已满 |
| 502 | LLM_INVALID_OUTPUT | 模型未返回可验证结构 |
| 503 | LLM_UNAVAILABLE | 密钥、连接、超时或上游不可用 |

意图解析失败不生成菜单；解释失败可回退到同一份已验证事实。营养只作定性解释，不判断治疗效果或个人摄入达标。菜谱缺少可靠总耗时，明确时间上限会要求澄清。

## 真实数据验收与回归

`python -m evaluation.real_data`：全部真实档案及原始对话在本地验收，生成 evaluation/REAL_DATA_REPORT.md。`python -m evaluation.offline --unprepared`：原始对话不预置用餐信息，验证缺字段澄清。`python -m evaluation.offline`：显式配置测试用餐上下文，保留对规划下游的回归覆盖；上下文不是原始用户输入。以上均不调用外部模型，也不评价真实模型 NLU。

原始外部回放模块 evaluation.replay 仅保留旧代码；当前 Demo 没有原始用户入口，适配器也拒绝原始画像，不执行该回放。
