# 比赛 Demo API（v0.3.0）

## 10月5日新增：有限本地生成兜底

默认原生及兼容接口已可在原库无合规豆腐主体菜、且本餐明确想要豆腐时补一条本地清蒸豆腐方案；先检索后生成并再次核全部已知硬要求。只支持午/晚餐基础豆腐提案，非任意菜式生成；未知库存/过敏/明确做法不能靠造菜绕过。已有合规库内主体时不生成；只读/继续不重排，局部换菜不扩大范围。

已排餐后明确追加豆腐偏好也支持该兜底，不只限首次规划。默认同输入四轮流程已验证：旧追加仍缺豆腐主体，新追加只换蛋白菜位；之后解释/继续不变。完整前后配料/做法及剩余问题`runtime/generated_append_20261005/human-review/report.md`，非新真实模型解析。

`menu[].source`会明确写“本地新生成方案（待试做，非原菜谱库）”。`provenance`新增`origin`（catalog/generated）与`generator_version`（原库为null）；新生成菜的`source_row`为null，不伪造CSV行。原库记录仍保原行和内容指纹。`nutrition.source_row`和对应`ingredient_contributions[].source_row`亦允许null；客户端不能将null当第0行。生成菜名及卡片有明显标识，正文不可省略生成来源/待试做/用量份数营养未知。接口不接受用户/模型直接传入菜谱或生成来源字段。

新方案只有老豆腐和水，无估计克数、两人份量、设备程序或功效标签；营养解释仅依据提案声明，不是实测或原方证据。真实NLU仍需已配置模型，生成兜底本身不调用模型。完整本地前后报告`runtime/generated_tofu_20261005/human-review/report.md`；旧“schema未变/H02未补主体”保为历史，新版镜像与平台验收待完成。

10月5日当前默认补充：普通盐/酱油存在仅作待核提示，不直接判断高钠或扣健康排序；食物参考按主体角色作用域及已有覆盖饱和处理。明确健康目标别名归一化，食材偏好未满足必须在本地正文披露。用户已接受M01/M05具体菜单，H02只有混合菜含豆腐的部分改善、豆腐主体缺口仍保留。接口/schema未变。当前启动预检与模型替身回归见[交付预检](DELIVERY_PREFLIGHT_20261005.md)，不是平台或真实模型验收。

默认护心排菜（2026-10-04）：明确护心目标下，末端可用原方有菜名/食材/角色依据的鱼、豆腐或瘦禽肉替换已知红肉主体。普通含钠来源与一般方法代理可取舍，但过敏/不辣/素食、菜汤数、点名食材/做法、用户ID与局部权限不松。`reason`/`warnings`必保主体取舍及摄入/品牌/份数未知，不是营养评分或健康认证。无HTTP开关/新schema；原生/兼容共用默认Planner，完整原方只本地。

未配置本地档案时，默认服务仅加载3个手写合成画像与原始菜谱CSV。支持显式`LOCAL_PROFILE_PATH`本地装载50个官方ID，详见[配置与边界](LOCAL_OFFICIAL_IDS.md)；不读取官方对话。档案仍保留original并仅本地匹配，默认模型只解析用户文字及白名单结构，源菜谱/助手解释不外发，说明本地生成；见[模型边界](USER_ONLY_MODEL_BOUNDARY.md)。没有从HTTP传入或覆盖data_scope的接口。

### 默认下一餐行为（2026-10-04）

明确“安排下一餐/安排明天晚餐”等肯定规划命令开启新餐，保留当前饮食限制并参考同一user_id最近8份已推荐菜单，在同等适配候选中优先轮换；不是实际食用记录，也不保证完全不重复。普通“继续”、解释、否定/疑问及定点换菜不创建新餐，不扩大权限。历史只按ID隔离、本地保存，模型不能指定历史用户/开关。原生及兼容入口均使用此默认路径；当前固定M02四餐已消除旧第四餐的两道相邻重复，但原方实用性仍待人工，非全面去重保证。其他求解、扩池与内容轮换实验不因此启用。

2026-10-05 融合版继续使用 `schema_version=2.0`。会话新增字段都有默认值，旧 SQLite 快照和请求缓存可继续读取；现有请求字段与 JSON/SSE 传输约定保持兼容。

`conversation_state` 包含 `pending_menu_counts`、`pending_revoke_exclusion`、`constraint_history`、`menu_history`、`rejection_actions`；参餐者的 `participation_basis` 区分明确参餐与档案待关联。问题列表按本轮实际缺失字段生成，不依赖回复文案前缀。

普通忌口撤销需要先提出目标，再明确确认或取消；过敏不可通过该路径删除。当前历史恢复只支持最初的总菜数、最初的整份菜单；两者范围独立，不恢复整个会话，不支持任意上一轮撤销。恢复菜单以原 recipe IDs 为准，并重新验证当前限制。

成功解释必需包含已验证的本轮操作、当前要求、来源和定性营养边界；存在档案待关联或多人限制时保留对应事实。可选检索后端失败时 `/chat` 返回 HTTP 503 / `RETRIEVAL_UNAVAILABLE`，兼容入口返回 HTTP 503 / `retrieval_unavailable`；`stream=true` 也返回 JSON 错误，不能以空 SSE 成功掩盖失败。当前限制已保存，可稍后继续同一会话。

用户界面为 `http://localhost:8080`，演示页为 `/demo`；前端请求 `/api/` 前缀，由 Nginx 转发到以下原有 API。原 `/health`、`/chat`、`/demo/profiles`、`/docs` 兼容入口继续可用。评测方可调用 OpenAI Chat Completions 兼容入口 `/v1/chat/completions`。内部 `/chat` 响应 schema_version 仍为 2.0。

单独调试后端（不包含前端）的启动命令：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Compose部署的接口文档：`http://localhost:8080/docs`；单独后端调试对应 `http://localhost:8000/docs`。本版本无鉴权，限本机 Demo、单 worker；正式公网部署必须在反向代理或 API 网关增加 HTTPS、鉴权、限流及访问日志。`POST /chat` 返回完整业务 JSON；`POST /v1/chat/completions` 可返回完整文本或 SSE。

## GET /health

返回 status、recipe_count、profile_count、profile_data_scope、llm_configured、model、tools。默认应为 2000 条菜谱、3 个 synthetic 画像和四个业务工具。`llm_configured=true` 只说明存在配置，不证明远端连通。

## GET /demo/profiles

列出当前可用合成画像的 user_id、label、allergies、health_goals、preferences；不会列出注入测试目录中的原始画像。

- 900001：基础用餐与主动澄清。
- 900002：海鲜/花生过敏及控糖偏好。
- 900003：降压、护心的定性偏好及原配方风险说明；不保证摄入达标或健康功效。

所有信息都是手写的演示资料，不对应真实个人。

### 比赛用户ID接入状态（2026-10-04）

按官方1–50整数ID准备，显式本地JSON源`id`规范化为`user_id`，不按位置/偏移/随机匹配；未配置仍404。用户已确认仅用户文本解析＋本地解释，配置后不再因original直接403，档案仍original且不发模型。原生`user_id: 1`、兼容`user: "1"`是现有表达，平台请求JSON仍缺，非已验平台协议。Key自备不进包；用户自己输入的健康/菜名仍可能外发，见[模型边界](USER_ONLY_MODEL_BOUNDARY.md)。本轮MockTransport非真实模型/测评通过。

## POST /v1/chat/completions

`aecf1ae`新增原机器程序待核的必保解释事实。即使可选解释只选开场、或普通继续保菜单，兼容文本也会说明不能由菜名补做法/温度/时长/熟度，不只放在原生warnings。实际共享晚餐小修与限制见`PR_SHARED_SOUP_ENTREE.md`；不是完整程序已补齐或平台通过声明。

此入口实现 OpenAI Chat Completions 的文本子集，供通用 SDK、评测脚本和流式客户端接入。它复用 `/chat` 的同一套档案、会话、检索、规划和硬约束校验。成功时从已验证的 `menu` 确定性输出每道菜的编号、名称、菜谱 ID 和来源，再附上 `reason`；非流式与 SSE 使用同一文本渲染器，菜单完整性不依赖解释模型选择哪些事实。澄清或无可行菜单时只输出相应原因，不展示旧菜单。接口不会把未经约束校验的模型 token 直接转发给客户端。

当前模型名固定为 `fangtai-meal-agent`。每个请求只接受一条 `role=user` 的纯文本消息，表示“当前新增的一轮”；多轮上下文由服务端会话保存，客户端应复用返回的 `X-Session-ID`，而不是重复上传全部历史。示例：

```json
{
  "model": "fangtai-meal-agent",
  "messages": [
    {"role": "user", "content": "2人晚餐，没有其他忌口，安排三道菜。"}
  ],
  "user": "900001",
  "stream": true,
  "request_id": "judge-case-01-turn-01"
}
```

| 请求字段 | 约束 |
| --- | --- |
| model | 必须为 `fangtai-meal-agent` |
| messages | 恰好一条 user 纯文本消息；内容去除首尾空白后为 1—2000 字符 |
| user | 用户ID字符串，例如 `"900001"`；也可改用 `context.user_id` 或 `X-User-ID` |
| stream | 默认 false；true 时响应为 `text/event-stream` |
| stream_options | 仅接受 `{"include_usage": false}`，且只可与 stream=true 同用 |
| session_id | 可选；也可改用 `context.session_id` 或 `X-Session-ID` |
| request_id | 可选幂等键；也可改用 `context.client_turn_id` 或 `X-Client-Request-Id` |
| context | 方太扩展对象，可含 user_id、session_id、client_turn_id |

同一个含义若从正文和请求头重复提供，值必须完全一致，否则返回 409 `identity_conflict`。用户ID必填；session_id 首轮省略，后续必须复用。每次成功响应都带 `X-Session-ID` 和仅用于链路追踪的 `X-Request-ID`。幂等重试应保留原来的业务 request_id；`X-Request-ID` 每个 HTTP 响应都会重新生成，不能用作业务幂等键。

成功响应还可带标准 `Server-Timing`，其中 `agent_total`、`agent_parse`、`planning` 和 `explanation` 是服务端内部阶段耗时（毫秒）。该响应头用于定位性能瓶颈，不包含 TTFT，也不能代替客户端从发出请求到收到第一个非空正文块的实际测量。

### 非流式响应

stream=false 时返回标准 Chat Completion 文本子集：

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1789999999,
  "model": "fangtai-meal-agent",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "..."},
      "logprobs": null,
      "finish_reason": "stop"
    }
  ]
}
```

### SSE 流式响应

stream=true 时，每条事件使用 `data: <JSON>\n\n`。首块声明 assistant 角色，正文位于后续 `choices[0].delta.content`；最后一个 JSON 块的 `finish_reason` 为 `stop`，随后发送 `data: [DONE]`。同一响应的所有块共用 id、created 和 model。Nginx 对 `/v1/` 关闭缓冲，避免代理聚合 SSE 分块。

服务端会先完成需求解析、菜谱检索、套餐规划和硬约束校验，再开始 SSE。这样可以避免已经向客户端发出 200 后才发现过敏冲突或模型错误；代价是首 Token 时间包含上述安全处理耗时。当前没有可信 token 计量，因此拒绝 `stream_options.include_usage=true`，不会伪造 usage 数值。

### 兼容范围与错误

当前不接受 system/assistant 历史消息、图片、音频、tool_calls、n、temperature 等生成参数，也不支持客户端一次提交完整历史。多条 messages 或未知字段会返回 422，而不会被静默忽略。错误统一使用：

```json
{
  "error": {
    "message": "可安全展示的错误信息",
    "type": "invalid_request_error",
    "param": "messages",
    "code": "invalid_request"
  }
}
```

业务状态映射为404用户/会话不存在、409身份/会话冲突、422请求不支持、429容量已满、502模型输出不可验证、503模型不可用。旧403原档案禁止外发错误码仍兼容保留，但当前默认仅用户文本适配器不因original直接触发；不会外发档案。所有规划错误在SSE头之前处理。

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
| user_id | 必填正整数；默认目录仅存在 900001—900003，不会自动选人或匹配画像 |
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

个人无法映射的过敏原保存在该人物的 `pending_allergy_terms`，同轮已知过敏仍先保存。只要参餐者还有未解决项，就持续返回澄清；无关追问和服务重启不会清除它。解析协议的 `diner_updates[].allergy_clarifications` 用“原待确认词 → 本轮明确食材列表”逐项解释，不能清除已知过敏或替另一人解除待确认项。

对于明确指向已登记人物、但未给出食材的简短过敏声明，`diners[].pending_allergy` 保存该人物的待确认状态；后续只有对应人物明确补充具体食材才能解除。人物归属无法可靠确认时仍保留整桌待确认，不从其他人的新增过敏反推答案。

当用户没有明确菜数时，工程默认值为：1—2 人 3 道且无汤，3—4 人 4 道含 1 汤，5—6 人 5 道含 1 汤，7—8 人 6 道含 1 汤。用户明确总菜数或汤数后，后续人数变化不覆盖该结构。已确认总人数小于实名参餐者数量、称呼映射不唯一、或个人过敏词无法可靠映射时，接口返回 `clarification_required`，不会执行检索和规划。

旧快照若没有 `menu_structure_explicit` 而已保存菜数或汤数，读取与幂等重放时保守地标记为需要保留的结构。新会话仍按现有默认规则工作；升级前已保存的结构不会因人数变化被默认值覆盖。

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

### 套餐搭配说明

成功响应不会返回内部搭配分或“系统自评”。`reason` 只陈述可从入选菜谱追溯的事实：各类菜品数量、可识别烹饪方式，以及热菜、冷食和未知的文字证据数量。类别和做法来自规范化菜谱元数据；冷热只在菜名或步骤出现“凉拌、冷拌、冰镇、冷藏、放凉、晾凉”等明确冷食词，或存在蒸、煮、炖、炒、烤、煎、炸、焖等热加工方式时归类，其余保持未知。

规划器内部使用确定性的结构排序信号比较候选，但该信号不进入 API，也不作为营养、健康或专家评分。普通解释、只读继续和幂等重试保留原菜单；新规划/明确追加要求可在授权范围做有界软参考修复，不保证只交换一次，也不要求每个旧软指标都不下降。硬约束、菜汤数、明确食材/方法、user_id与指定菜位权限仍保护。

当前代码`b1d4acb`的默认多人午晚餐在已有汤、且没点名要粥时，可同角色用源配方熟米饭替换额外粥，未改原料或步骤。默认HTTP已经有同输入第5道“藜麦南瓜小米粥→基础煮燕麦饭”的对照，整餐质量仍待人工；有过敏/明确粥/食材/方法/局部范围冲突时不靠该默认放宽。跨餐轮换及联合solver仍未默认启用，不能由这项变化推断重复推荐已解决。

比赛技术方案见[现行技术方案](TECHNICAL_SOLUTION.md)。默认只含三合成ID，原50ID接口接入及平台具体请求格式尚未确认；当前接口存在不等于平台验收已通过。

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

`python -m evaluation.regression_suite --base-url http://localhost:8080`：执行公开的版本化合成回归集，对 `/chat` 的菜谱来源、硬约束、多人适配和多轮最小修改做结构化断言，并通过 SSE 重放性能子集。每次输出 JSON、Markdown 和 JSONL 报告；报告只给出回归证据，不把用例全过换算为质量满分，详见 [REGRESSION.md](REGRESSION.md)。

`python -m evaluation.real_data`：全部真实档案及原始对话在本地验收，生成 evaluation/REAL_DATA_REPORT.md。`python -m evaluation.offline --unprepared`：原始对话不预置用餐信息，验证缺字段澄清。`python -m evaluation.offline`：显式配置测试用餐上下文，保留对规划下游的回归覆盖；上下文不是原始用户输入。以上均不调用外部模型，也不评价真实模型 NLU。

原始外部回放模块 evaluation.replay 仅保留旧代码；当前 Demo 没有原始用户入口，适配器也拒绝原始画像，不执行该回放。
