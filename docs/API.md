# 比赛 Demo API（v0.3.0）

本文对应2026-10-10冻结候选的公开源码交付。已部署基准按用户指定的源码清单核对；本轮不连接服务器、不更改配置或重跑真实模型。源码、镜像与验证范围见[交付记录](RELEASE_DELIVERY_20261010.md)。以下带日期的旧回放只代表当时观测，不等于本次重跑或官方评分。

## 10月5日新增：有限本地生成兜底

10月5日加入的默认原生及兼容路径可在原库无合规豆腐主体菜、且本餐明确想要豆腐时补一条本地清蒸豆腐方案；先检索后生成并再次核全部已知硬要求。当前源码还包含显式煎豆腐请求和不辣凉粉缺口的有限独立配方模板，非任意菜式生成；未知库存/过敏/明确做法不能靠造菜绕过。已有合规库内主体时不生成；只读/继续不重排，局部换菜不扩大范围。

预置模板“黄瓜清拌凉粉（新生成）”只在明确想要凉粉、不辣且原库没有合规正餐凉粉时提出，经现行规则复核。它不是原2000条之一，也不是删去原菜辣料后冒充原方；独立标记`generated_…`、`origin=generated`、`generator_version=local-non-spicy-liangfen-v1`及`source_row=null`，披露待试做、配比按产品说明核对及份量/总耗时未知。原库菜保留原记录、来源行和指纹。当前云模板关闭的是可选付费菜谱提案，不代表这些本地模板属于原库。

已排餐后明确追加豆腐偏好也支持该兜底，不只限首次规划。默认同输入四轮流程已验证：旧追加仍缺豆腐主体，新追加只换蛋白菜位；之后解释/继续不变。完整前后配料/做法及剩余问题`本机受控证据（不公开）`，非新真实模型解析。

`menu[].source`会明确写“本地新生成方案（待试做，非原菜谱库）”。`provenance`新增`origin`（catalog/generated）与`generator_version`（原库为null）；新生成菜的`source_row`为null，不伪造CSV行。原库记录仍保原行和内容指纹。`nutrition.source_row`和对应`ingredient_contributions[].source_row`亦允许null；客户端不能将null当第0行。生成菜名及卡片有明显标识，正文不可省略生成来源/待试做/用量份数营养未知。接口不接受用户/模型直接传入菜谱或生成来源字段。

10月5日清蒸豆腐方案只有老豆腐和水，无估计克数、两人份量、设备程序或功效标签；营养解释仅依据提案声明，不是实测或原方证据。真实NLU仍需已配置模型，本地模板兜底本身不调用模型。完整本地前后报告`本机受控证据（不公开）`；旧“schema未变/H02未补主体”保为历史，该报告不能证明今天的镜像或平台验收。

10月5日历史补充：普通盐/酱油存在仅作待核提示，不直接判断高钠或扣健康排序；食物参考按主体角色作用域及已有覆盖饱和处理。明确健康目标别名归一化，食材偏好未满足必须在本地正文披露。该阶段记录用户接受M01/M05具体菜单，生成兜底前H02仍缺豆腐主体；不能将此旧缺口描述当作当前默认模板的结论。该阶段接口/schema及启动预检与模型替身回归见[交付预检](DELIVERY_PREFLIGHT_20261005.md)，不是平台或真实模型验收。

默认护心排菜（2026-10-04）：明确护心目标下，末端可用原方有菜名/食材/角色依据的鱼、豆腐或瘦禽肉替换已知红肉主体。普通含钠来源与一般方法代理可取舍，但过敏/不辣/素食、菜汤数、点名食材/做法、用户ID与局部权限不松。`reason`/`warnings`必保主体取舍及摄入/品牌/份数未知，不是营养评分或健康认证。无HTTP开关/新schema；原生/兼容共用默认Planner，完整原方只本地。

未配置本地档案时，默认服务仅加载3个手写合成画像与原始菜谱CSV。支持显式`LOCAL_PROFILE_PATH`本地装载50个官方ID，详见[配置与边界](LOCAL_OFFICIAL_IDS.md)；不读取官方对话。档案仍保留original并仅本地匹配，默认模型只解析用户文字及白名单结构，源菜谱/助手解释不外发，说明本地生成；见[模型边界](USER_ONLY_MODEL_BOUNDARY.md)。没有从HTTP传入或覆盖data_scope的接口。

### 默认下一餐行为（2026-10-04）

明确“安排下一餐/安排明天晚餐”等肯定规划命令开启新餐，保留当前饮食限制并参考同一user_id最近8份已推荐菜单，在同等适配候选中优先轮换；不是实际食用记录，也不保证完全不重复。普通“继续”、解释、否定/疑问及定点换菜不创建新餐，不扩大权限。历史只按ID隔离、本地保存，模型不能指定历史用户/开关。当前`create_app`明确启用跨餐轮换，原生及兼容入口共用此路径。历史固定Intent的M02四餐回放消除了旧第四餐的两道相邻重复，只证明解析后的规划/编辑，不证明真实NLU、当前云版本复现或总体竞赛准确率；原方实用性仍待人工。联合solver、扩池等实验不因此启用。

2026-10-05 融合版继续使用 `schema_version=2.0`。会话新增字段都有默认值，旧 SQLite 快照和请求缓存可继续读取；现有请求字段与 JSON/SSE 传输约定保持兼容。

`conversation_state` 包含 `pending_menu_counts`、`pending_revoke_exclusion`、`constraint_history`、`menu_history`、`rejection_actions`；参餐者的 `participation_basis` 区分明确参餐与档案待关联。问题列表按本轮实际缺失字段生成，不依赖回复文案前缀。

普通忌口撤销需要先提出目标，再明确确认或取消；过敏不可通过该路径删除。当前历史恢复只支持最初的总菜数、最初的整份菜单；两者范围独立，不恢复整个会话，不支持任意上一轮撤销。恢复菜单以原 recipe IDs 为准，并重新验证当前限制。

成功解释必需包含已验证的本轮操作、当前要求、来源和定性营养边界；存在档案待关联或多人限制时保留对应事实。可选检索后端失败时 `/chat` 返回 HTTP 503 / `RETRIEVAL_UNAVAILABLE`，兼容入口返回 HTTP 503 / `retrieval_unavailable`；`stream=true` 也返回 JSON 错误，不能以空 SSE 成功掩盖失败。当前限制已保存，可稍后继续同一会话。

用户界面为 `http://localhost:8080`，演示页为 `/demo`；前端请求 `/api/` 前缀，由 Nginx 转发到以下原有 API。原 `/health`、`/chat`、`/demo/profiles`、`/docs` 兼容入口继续可用。评测方可调用 OpenAI Chat Completions 兼容入口 `/v1/chat/completions`。内部 `/chat` 响应 schema_version 仍为 2.0。

单独调试后端（不包含前端）的启动命令：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Compose部署的接口文档：`http://localhost:8080/docs`；单独后端调试对应 `http://localhost:8000/docs`。FastAPI 本身不校验 Bearer，本机 Demo 限本地访问；已有云网关负责聊天路由的 Bearer 校验、限流和访问日志，不能将后端端口直接公开。HTTPS 取决于部署模式：若地址使用 `http://`，Bearer token 和聊天正文没有 TLS 传输保护，认证不等于传输加密。本次输出改动不调整 TLS 或部署。`POST /chat` 返回完整业务 JSON；`POST /v1/chat/completions` 可返回完整文本或 SSE。

## GET /api-guide（HEAD 同路径）

返回固定的公开JSON调用说明：字段规则、占位符首轮/续轮示例、默认非流式和SSE行为、错误处理。后端路由与本地云Nginx模板均使用精确`/api-guide`，仅GET/HEAD公开，HEAD无正文；本文描述该候选源码的路由；具体环境是否开放，以其实际网关版本为准。它不读取档案、会话、配置或环境，不创建会话、不调用业务工具或模型，不回显查询参数、认证头或用户标识。其他方法返回405，不开放说明路径的子路径，也不扩大聊天路径的认证豁免。

错误消息可引用固定相对路径`/api-guide`；正常推荐content不加入协议说明。此说明不列出可用档案或真实访问令牌，所有尖括号身份/会话/令牌均须替换。已有`/docs`、`/health`及原生`/chat`保持原规则。

## GET /health

返回 status、recipe_count、profile_count、profile_data_scope、llm_configured、model、tools。未配置本地正式档案时应为2000条菜谱、3个synthetic画像和四个业务工具；显式配置正式档案时装载50个original画像，不能仍按3个合成画像判定。`llm_configured=true`只说明存在配置，不证明远端连通。

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

此入口实现 OpenAI Chat Completions 的文本子集，复用 `/chat` 的同一套档案、会话、检索、规划和硬约束校验。自然语言正文保留每道菜的编号、名称、菜谱 ID、来源和 `reason`；从 2026-10-09 的本地修改起，在正文末尾追加下述菜谱 JSON 区块。非流式与 SSE 使用同一份最终回答文本，不依赖解释模型选择哪些事实，也不转发未经约束校验的模型 token。原生 `/chat` 的 schema_version 2.0、`menu` 和 `reason` 不添加这个文本区块。

此格式采用用户选定的“先文本、后 ID/菜名 JSON 列表”验收约定，不声称已核实为赛事官方强制规范。当前部署是否包含本地改动需另行验收，本文件不能证明已上线或学校评测器已兼容。

### 必填、选填与最小首轮请求

实际请求Schema的required仅为`model`、`messages`；单条消息的required为`role`、`content`。入口还要求`user`、`context.user_id`或`X-User-ID`至少一个合法身份，不能只依据Schema宣称身份可完全省略。已交合同的`user`是档案ID字符串，正式目录ID为1–50；这是本项目的档案映射约定，不宣称完整OpenAI原生user语义或完全drop-in兼容。

按已交格式，最小首轮正文如下（保留POST、JSON Content-Type及云入口Bearer认证；尖括号必须替换）：

```json
{
  "model": "fangtai-meal-agent",
  "user": "<已授权的测试用户ID>",
  "messages": [{"role": "user", "content": "合成示例：1人晚餐，没有其他忌口。"}]
}
```

首轮可省略`stream`、`stream_options`、`context`、`request_id`和会话字段，省略stream默认false。续轮只添加原响应返回的`X-Session-ID`或等价会话位置，不强制添加其他扩展；`X-Request-ID`是响应追踪头，不要求作为请求字段传回。本轮未发现额外必填项，未放宽请求模型或身份/会话校验。

选填表示可省略，不表示任何显式值均合法。当前`user`、`context`、`stream_options`、顶层会话/幂等键及context内选填值可为null；null身份视为未提供，仍须从其他位置提供合法身份。`stream=null`、空ID、数字型user、冲突身份及未知字段继续拒绝，不默认选用户。

### 完整首轮请求与用户身份

当前模型名固定为 `fangtai-meal-agent`。每次请求只接受一条 `role=user` 的纯文本消息，表示当前新增的一轮。以下所有尖括号项都是占位符，不能原样执行；用户 ID 必须替换为调用方实际获授权、且目标目录中存在的正整数 ID 字符串，不默认选人或猜测真实身份。Bearer 使用本项目的独立访问令牌，不能填写模型密钥。

```http
POST /v1/chat/completions HTTP/1.1
Host: <部署域名或地址>
Authorization: Bearer <PUBLIC_API_TOKEN>
Content-Type: application/json
Accept: text/event-stream

{
  "model": "fangtai-meal-agent",
  "user": "<已授权的测试用户ID>",
  "messages": [
    {"role": "user", "content": "请为两位成年人推荐一顿晚餐，口味清淡，不要香菜。"}
  ],
  "stream": true
}
```

| 请求字段 | 约束 |
| --- | --- |
| model | 必须为 `fangtai-meal-agent` |
| messages | 恰好一条 user 纯文本消息；内容去除首尾空白后为 1—2000 字符 |
| user | 可选位置但用户身份必填；字符串匹配 `^[1-9][0-9]*$`，最长20字符，不能传 JSON 数字 |
| context.user_id | 可替代 user；必须是 JSON 正整数，不能是数字字符串、浮点或布尔 |
| X-User-ID | 可替代 user；请求头为 ASCII 数字且值大于0，按整数比较，可接受前导零 |
| stream | 可省略，默认 false；提供时必须是布尔，不能是null；true 时正常响应为 `text/event-stream` |
| stream_options | 可省略/null；非null允许空对象或 `{"include_usage": false}`，且只可与 stream=true 同用 |
| session_id | 可选正文顶层字段；也可用 `context.session_id` 或 `X-Session-ID`，均为32位小写十六进制字符串 |
| request_id | 可选业务幂等键，1—128字符；也可用 `context.client_turn_id` 或 `X-Client-Request-Id` |
| context | 方太扩展对象，可含 user_id、session_id、client_turn_id |

上述身份位置没有“用某一来源覆盖另一来源”的优先级；重复提供者经类型/整数解析后必须相等，不一致返回409 `identity_conflict`。缺少全部用户位置返回422 `missing_user`，`param=user`；其他无效结构返回422 `invalid_request` 或相应请求头错误码。未知字段、多条 messages、system/assistant 历史消息均不支持。

所选 ID 可能加载预设档案并触发模型解析。当前适配器仅发送用户自写消息、有限用户消息历史及受限状态字段，档案和助手解释仍在本地；用户主动输入的敏感信息可能进入模型处理。不能因为资料来自赛方就推定全部为虚构数据，也不能把标注“合成测试”的消息当成对应档案已合成的证明。

### 下一轮与幂等

正常响应返回 `X-Session-ID`，客户端保存它，下一轮只发送新消息并复用该头，不需也不允许重发全部历史：

```http
POST /v1/chat/completions HTTP/1.1
Host: <部署域名或地址>
Authorization: Bearer <PUBLIC_API_TOKEN>
Content-Type: application/json
Accept: text/event-stream
X-Session-ID: <首轮响应返回的会话ID>

{
  "model": "fangtai-meal-agent",
  "user": "<已授权的测试用户ID>",
  "messages": [
    {"role": "user", "content": "不要豆腐，其他要求不变"}
  ],
  "stream": true
}
```

第二轮用户 ID 必须与首轮相同。可等价使用正文顶层 `session_id` 或 `context.session_id`；重复会话位置不一致返回409 `identity_conflict`，跨用户续用返回409 `session_conflict`，未知会话返回404 `not_found`。省略会话 ID 表示首次请求，不能借此续用历史。

`request_id` 是幂等标识，不是会话 ID。相同用户、会话、消息与幂等键，在版本未前进时重放保存的 `ChatResult`，不重复解析或追加第二份尾部；改消息复用键或重放已过期版本返回409 `session_conflict`。首轮有 request_id 而没有会话 ID 时，服务端以用户和该键派生会话 ID，支持合法首轮重放。新操作使用新键并保持原会话。`context.client_turn_id`、`X-Client-Request-Id` 与 request_id 并存时也必须一致。

每次正常 HTTP 响应带新生成的 `X-Request-ID`，仅用于链路追踪，不是业务幂等键。重放的 completion ID 和时间可以重新生成，但回答文本不变。

成功响应还可带标准 `Server-Timing`，其中 `agent_total`、`agent_parse`、`planning` 和 `explanation` 是服务端内部阶段耗时（毫秒）。该响应头用于定位性能瓶颈，不包含 TTFT，也不能代替客户端从发出请求到收到第一个非空正文块的实际测量。

### 最终回答中的菜谱 JSON

仅当 `ChatResult.status="ok"`、`conversation_state.menu_valid=true` 且本轮 `menu` 与状态中 `menu_ids` 一致时，按 `menu` 原顺序提取每项的 `recipe_id`、`name`。不重新检索、规划、调用模型或从自然语言反推 ID；不混入替换建议、候选、未采用生成菜或历史菜单。同名不同 ID 不去重，已采用生成菜保留真实 `generated_…` 标识。

JSON 顶层是数组，每项恰好两个字段，固定顺序为 recipe_id、name；用标准 JSON 序列化，中文保留，引号、反斜杠及换行按 JSON 转义。以下 ID、菜名及来源是完全合成的离线 fixture，用于说明格式，不代表实际库记录或线上推荐：

````text
本餐菜单：
1. 同名菜（菜谱ID：synthetic_z；来源：方太菜谱库）
2. 同名菜（菜谱ID：synthetic_a；来源：方太菜谱库）
3. 新生成蔬菜方案（菜谱ID：generated_synthetic_final；来源：本地新生成方案（待试做，非原菜谱库））

本轮菜单已经核验。

【菜谱JSON】
```json
[{"recipe_id":"synthetic_z","name":"同名菜"},{"recipe_id":"synthetic_a","name":"同名菜"},{"recipe_id":"generated_synthetic_final","name":"新生成蔬菜方案"}]
```
````

`clarification_required`、`no_feasible_menu` 为正常业务响应，保留原因/追问，明确本次无菜单，并输出空数组。即使旧状态或非成功结果携带菜单也不能补入；本轮明确重新核验并保留原菜单仍输出那个当前菜单。完全合成的空菜单例子：

````text
请确认用餐人数。
本次未返回菜单。

【菜谱JSON】
```json
[]
```
````

成功状态却缺少可用菜单、状态 ID 与菜单不一致，或最终 ID/name 缺失、非字符串、空白、含无法编码的孤立 Unicode 代理项时，返回500 `invalid_menu_result` / `server_error`。不猜测值、不改写菜单，也不以 `[]` 包装损坏的成功结果；新增呈现校验在创建 SSE 成功响应前完成。

当前 CSV 表头为“名称、食材清单、烹饪步骤、label”，没有官方 ID 列。库内 recipe_id 是项目按这四个原文来源字段的内容指纹派生的内部稳定标识，完全重复行使用出现序号后缀，不是赛事或来源方分配的 ID。生成菜不是库内菜；此列表只有 ID/name，来源解释仍由原正文和原生卡片提供，不扩展新 JSON 字段。

### 非流式响应

非流式请求与上述完整首轮请求一致，只将 `stream` 改为 `false`，`Accept` 改为 `application/json`。区块位于 `choices[0].message.content`，与同一领域结果的全部 SSE content 拼接值逐字一致。以下为上述合成菜单的完整响应示例；JSON 中的 `\n`/`\"` 是外层响应对 content 的正常转义：

```json
{
  "id": "chatcmpl-synthetic",
  "object": "chat.completion",
  "created": 100,
  "model": "fangtai-meal-agent",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "本餐菜单：\n1. 同名菜（菜谱ID：synthetic_z；来源：方太菜谱库）\n2. 同名菜（菜谱ID：synthetic_a；来源：方太菜谱库）\n3. 新生成蔬菜方案（菜谱ID：generated_synthetic_final；来源：本地新生成方案（待试做，非原菜谱库））\n\n本轮菜单已经核验。\n\n【菜谱JSON】\n```json\n[{\"recipe_id\":\"synthetic_z\",\"name\":\"同名菜\"},{\"recipe_id\":\"synthetic_a\",\"name\":\"同名菜\"},{\"recipe_id\":\"generated_synthetic_final\",\"name\":\"新生成蔬菜方案\"}]\n```"},
      "logprobs": null,
      "finish_reason": "stop"
    }
  ]
}
```

### SSE 流式响应

stream=true 时，每条事件使用 `data: <JSON>\n\n`，首块声明 assistant 角色。正常顺序是：原自然语言正文 content → JSON 尾部所有 content → 空 delta 且 `finish_reason="stop"` → 恰好一次 `data: [DONE]`。stop/DONE 后没有内容。同一响应的所有块共用 id、created 和 model；尾部仍在普通 `choices[0].delta.content` 中，不添加 `event: recipes` 或自定义顶层 recipes 字段。当前按24个 Python 字符分块，JSON、转义对及 UTF-8 网络字节都可能跨块；分块边界不是业务 JSON 边界。Nginx 对流式路由关闭缓冲。

服务端先完成需求解析、检索、规划、核验及本次纯呈现校验，再开始 SSE；这是完成后分块输出，不是模型 token 实时透传，本次没有 TTFT 改善实测。拒绝 `stream_options.include_usage=true`，不伪造 usage。流开始后的迭代异常或客户端取消保持原中断语义，不补造尾部、正常 stop 或 DONE；离线取消测试验证可观察的 ASGI 输出边界，不代替生产代理和网络验收。

### 客户端解析顺序

1. 按 SSE 规则组装完整事件，并用增量 UTF-8 解码；一次网络 read 不一定等于事件或完整中文。
2. 遇到 `[DONE]` 终止，对其他 data 解析 OpenAI JSON 外壳；正常完成须观察到 stop 和一次 DONE。
3. 依序拼接目标 choice（当前 index=0）的 `delta.content`，不要把 delta、choices 或单个 chunk 当成菜谱数组。
4. 正常完成后提取最后一个完整且位于文本末尾的约定尾部：空行、`【菜谱JSON】`、换行、fenced `json` 区块；结束围栏后没有其他文字。原正文/菜名可能含类似标记，不逐 chunk 正则搜索，也不删除前面的合法正文。
5. 对区块内容做 JSON 解析，确认顶层数组、每项只有 recipe_id/name 且均为非空字符串。`[]` 表示本次正常业务响应无菜单；HTTP 错误、缺少正常终止或中断流不能解释成成功空菜单。
6. 非流式先解析 HTTP JSON，再对 `choices[0].message.content` 使用相同尾部解析；不要把外层 JSON 对 content 的转义当成额外序列化的菜谱字符串。

### 兼容范围与错误

当前不接受 system/assistant 历史消息、图片、音频、tool_calls、n、temperature 等生成参数，也不支持客户端一次提交完整历史。多条 messages 或未知字段返回422。FastAPI 兼容入口已处理的应用错误使用原有外壳，不追加成功式 JSON 菜单区块，也不改成 HTTP 200：

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

业务状态映射为404用户/会话不存在、409身份/会话冲突、422缺用户或请求不支持、429容量已满、500最终呈现数据不合法、502模型输出不可验证、503模型/检索不可用。旧403 `original_profile_blocked` 错误码仍保留，但当前默认用户文本适配器不因 original 直接触发。缺失/无效字段错误指出`param`、合法填写方式及`/api-guide`，不回显无效字段值或默认选择身份。

本地云Nginx模板的401仍为令牌缺失/错误，`code=unauthorized`保持不变，现补齐`message/type/param/code`，其中`type=authentication_error`、`param=Authorization`，消息说明使用本项目独立访问令牌并指向`/api-guide`。原Bearer验证、入口限流、429状态及后端错误透传未变，没有全局拦截或把错误包装成成功SSE。历史模板401仅有code/message；上轮因Docker Engine不可用未实测，本轮2026-10-10已用实际Nginx 1.30.5、临时假令牌与模拟后端完成本地隔离配置/HTTP验收，结果见[隔离验收报告](RELEASE_DELIVERY_20261010.md)。这不是现有云网关或真实业务部署验收。

四字段JSON保证仅限兼容入口已处理的应用错误及上述网关401，不承诺所有非200响应都采用该结构。原网关限流、路由/方法错误、代理失败或未捕获异常可能有其他正文格式。客户端先检查HTTP状态码与Content-Type，再决定是否解析JSON及其结构；解析失败或非JSON不能当作成功菜单或SSE。本轮实际限流429为text/html，80请求突发中22个200、58个429，冷却后恢复；只证明本地单批冒烟，不代表生产吞吐量或负载能力。静态模板和后端模拟401不能替代实际Nginx验收。

菜谱输出格式的变化仍只发生在回答文本末尾。依赖旧 content 以 reason 结尾或只包含自然语言的客户端应更新解析；外层 completion/SSE 结构、事件顺序、模型字段、请求身份、会话绑定与原生 `/chat` 保持原合同。本轮只新增公开说明及安全错误提示，不改变推荐正文。线上版本、真实身份、云网关/TLS及学校解析器仍需另行授权后验证。

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
| reason | 由本地核验事实组织；当前默认适配器不发解释模型请求 |
| constraints | 已确认约束及尚待确认信息的中文说明 |
| conversation_state | 会话 ID、版本、确认字段、约束、菜单有效性、rejected_recipe_ids 及有限历史 |
| diner_suitability | 当前参餐者逐人已知约束核验；不把未知信息写成已满足 |
| clarification_questions | field、prompt、options，可直接用于前端提问 |
| nutrition_analysis | 整餐定性组成、目标匹配、食材贡献与风险；无菜单时 null |
| replacement_suggestions | 适用于指定槽位的库内候选，不自动应用 |
| warnings | 未支持目标、数据不足等提示；其他适配器的解释异常可保留事实回退提示 |
| tool_calls | 真实工具名及计数，不包含原始健康档案 |
| timings_ms | 本轮解析、检索规则规划、解释和总耗时；不含等待锁的时间 |
| explanation_source | 当前默认为 verified_template；兼容旧/其他适配器的 deepseek_verified_facts 标签，不据此推定本轮有解释模型请求 |

### 套餐搭配说明

成功响应不会返回内部搭配分或“系统自评”。`reason` 只陈述可从入选菜谱追溯的事实：各类菜品数量、可识别烹饪方式，以及热菜、冷食和未知的文字证据数量。类别和做法来自规范化菜谱元数据；冷热只在菜名或步骤出现“凉拌、冷拌、冰镇、冷藏、放凉、晾凉”等明确冷食词，或存在蒸、煮、炖、炒、烤、煎、炸、焖等热加工方式时归类，其余保持未知。

规划器内部使用确定性的结构排序信号比较候选，但该信号不进入 API，也不作为营养、健康或专家评分。普通解释、只读继续和幂等重试保留原菜单；新规划/明确追加要求可在授权范围做有界软参考修复，不保证只交换一次，也不要求每个旧软指标都不下降。硬约束、菜汤数、明确食材/方法、user_id与指定菜位权限仍保护。

历史代码`b1d4acb`的默认多人午晚餐在已有汤、且没点名要粥时，可同角色用源配方熟米饭替换额外粥，未改原料或步骤。当时默认HTTP有固定输入第5道“藜麦南瓜小米粥→基础煮燕麦饭”的对照，整餐质量仍待人工；有过敏/明确粥/食材/方法/局部范围冲突时不靠该默认放宽。当时“跨餐轮换未默认启用”的结论不适用于当前`create_app`；联合solver仍不在默认入口，不能由历史对照推断全面去重、真实NLU或当前云端已验收。

比赛技术方案见[现行技术方案](TECHNICAL_SOLUTION.md)。未配置正式档案时仅有三个合成ID；显式`LOCAL_PROFILE_PATH`的50个正式ID接入已实现，10月7日公网记录确认当时装载1–50的original档案。今天的实际挂载/版本未复核，平台具体请求格式仍待确认；接口存在不等于平台验收通过。

### 菜品卡片

menu 和 replacement_suggestions 中每项包含：

| 字段 | 用途 |
| --- | --- |
| slot / recipe_id / name | 位置与真实菜谱身份 |
| ingredients / steps | 兼容旧客户端的食材名列表和原始步骤全文 |
| ingredient_details | name、raw、quantity、unit；未知数量/单位保持 null |
| cooking_steps | 按原始换行分段的 number、description；单段原文保留为一步，不编步骤 |
| card | title、subtitle、低风险 badges；未知 image_url、cooking_minutes、servings 为 null |
| provenance | recipe_id、source_row、fingerprint、origin、generator_version；原库追溯CSV，独立生成模板标明来源且source_row为null |
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
| 403 | ORIGINAL_PROFILE_BLOCKED | 保留旧/其他适配器主动拦截时的错误映射；当前默认用户文本适配器不因original直接触发 |
| 404 | NOT_FOUND | 用户或显式指定的会话不存在 |
| 409 | SESSION_CONFLICT | 会话归属、版本或重试冲突 |
| 422 | FastAPI 校验详情 | 非法字段、格式或额外字段 |
| 429 | BUSY | 进程容量已满 |
| 502 | LLM_INVALID_OUTPUT | 模型未返回可验证结构 |
| 503 | LLM_UNAVAILABLE | 密钥、连接、超时或上游不可用 |

意图解析失败不生成菜单；当前默认解释直接保留本地核验事实，不额外请求解释模型。其他适配器的解释异常仍可回退到同一份已验证事实。营养只作定性解释，不判断治疗效果或个人摄入达标。菜谱缺少可靠总耗时，明确时间上限会要求澄清。

## 真实数据验收与回归

`python -m evaluation.regression_suite --base-url http://localhost:8080`：执行公开的版本化合成回归集，对 `/chat` 的菜谱来源、硬约束、多人适配和多轮最小修改做结构化断言，并通过 SSE 重放性能子集。每次输出 JSON、Markdown 和 JSONL 报告；报告只给出回归证据，不把用例全过换算为质量满分，详见 [REGRESSION.md](REGRESSION.md)。

`python -m evaluation.real_data`：全部真实档案及原始对话在本地验收，生成 evaluation/REAL_DATA_REPORT.md。`python -m evaluation.offline --unprepared`：原始对话不预置用餐信息，验证缺字段澄清。`python -m evaluation.offline`：显式配置测试用餐上下文，保留对规划下游的回归覆盖；上下文不是原始用户输入。以上均不调用外部模型，也不评价真实模型 NLU。

原始外部回放模块`evaluation.replay`仅保留旧代码，本次不执行。当前运行入口可显式装载original档案并在本地匹配，默认模型适配器只解析用户自写文字及白名单结构，不因original直接403，也不自动发送原档案或官方对话。上述离线脚本需另行授权原始资料；固定解析结果的历史回放仅证明解析后的规划/编辑，报告汇总不等于本次重跑，不证明真实NLU、今天的云版本复现或总体竞赛准确率。
