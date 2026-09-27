# DietAgent PR #2–#5 审查与维修报告

维修日期：2026-09-27（北京时间）  
维修基线：`ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc`  
维修分支：`fix/pr-review-20260927`  
范围：原审查 F1–F7；原 PR 功能保留，通过修复提交交付。

## 维修结论

原审查 F1–F7 均已修复并补充回归，完整后端测试 **477 passed**，Ruff 通过，独立交叉复审无剩余阻断项。此结论仅支持本轮代码修复候选，不替代真实模型或正式比赛接口验收。

## 维修内容

| 编号 | 修复方式 | 验证重点 |
|---|---|---|
| F1 | 保存已知个人过敏，将未知词持久化到对应人物；明确逐项映射后才解除该人物待确认项 | 已知＋未知、无关追问、重启、归属、追加与明确解决、离席后重返 |
| F2 | 适配器与服务共用过敏提及校验；接受顶层及人物字段，识别有限范围的既有限制引用；增加个人澄清协议 | 真实适配器＋MockTransport、服务组合路径，模糊新过敏仍澄清 |
| F3 | JSON 与 SSE 共用确定性文本渲染器，先输出已验证的每道菜编号、名称、菜谱 ID 和来源，再附解释 | 解释只选择 catalog 时仍有完整菜单；澄清不泄出旧菜单 |
| F4 | 新增版本化回归用例与验证器；核验人物预期事实、聚合限制以及独立菜谱证据 | 清空限制、归属错配、伪报通过、真实含虾菜单与替换建议必须失败 |
| F5 | 按明确出餐温度和步骤顺序判定冷热，证据不足保持 unknown | 四个真实预处理冷藏/放凉反例均判为 hot；真实冷菜保留 cold |
| F6 | 旧快照缺少结构来源标记时，保守保护其已有菜数、汤数；会话和请求重放都走升级逻辑 | 原四菜一汤修改人数或参餐者后仍为五道；新会话仍可采用人数默认值 |
| F7 | 性能有效性参与摘要和评分；无效测量不生成性能分与综合总分，显示成功/失败/未执行数 | 部分失败、缺失与跳过的性能数据不得伪装成满分 |

原 `regression_v1.json` 保留；新版结果必须连同数据集版本、哈希和验证器版本解释，不能与旧分数直接混用。内部诊断评分仍不是官方成绩。

## 验证结果

环境沿用审查时的 Python 3.12 临时环境与项目依赖；仅使用合成画像、模拟模型响应及仓库现有菜谱。

```text
python -m pytest -q
477 passed, 1 warning in 8.76s

python -m ruff check app pipelines evaluation tests
All checks passed!

git diff --check
通过

git diff --exit-code -- evaluation/cases/regression_v1.json
通过；原用例文件未修改
```

唯一警告为已有 Starlette TestClient / httpx 弃用提示。未运行真实 DeepSeek、Docker 部署或公网性能测试；本次没有以历史报告中的成绩替代新版实测。

关键反例已固化在 `tests/test_review_state.py`、`tests/test_review_parser.py`、现有 API / 模型适配器 / 冷热分类 / 评测器 / 会话测试中。覆盖包括：

- 个人已知＋未知过敏保存、重启、无关追问、追加限制、分别归属、离席再参餐、明确映射；无法归属的未说明过敏仍保守澄清。
- 真实适配器与服务组合验证个人花生过敏、照旧换菜、解释和明确解决；人物别名冲突返回安全解析错误，不泄出未处理异常。
- 解释仅选 `catalog` 时两种文本接口仍返回全部菜名；澄清响应不输出旧菜单。
- 真实菜谱中的四个冷热反例，以及真实冷菜与温度不确定样例。
- 删约束、错归属、真实含虾菜单、含虾替换建议、隐藏返回食材和伪造来源均不能骗过新版评测；性能 HTTP 503 或后续未执行会使计分无效。
- 旧 SQLite 格式和幂等缓存读取后保留原五道菜、一汤；当前显式默认结构行为保持。

### 集成测试中修正的一处旧测试误判

首次完整回归为 475 passed、1 failed，失败位于真实菜谱的牛肉偏好测试。新冷热排序使首份菜单已有“五香牛肉”（source_row 462，原料明确为“牛腱肉800克”），两轮保持相同菜单是正确行为；旧断言只搜索“牛肉”字样，漏认牛腱。

`tests/test_preference_updates.py` 现使用独立固定食材词检查配料/步骤，不调用被测规则引擎作判断，也不依赖菜名。仍要求：已覆盖偏好则换 0 道，未覆盖则只换 1 道，解释不得更改菜单。本次未修改偏好业务逻辑或放宽最少修改要求。另补齐“个人未说明食材 → 未知名称 → 明确映射”的回归后，最终全套为上述 477 项通过。


## 兼容性、部署与剩余边界

- 对旧 SQLite 快照执行读取时兼容升级，不清空持久卷，不删除原会话。缺失菜单结构来源的旧值采用保守保护；不会反推它原本是否由默认规则生成。
- 新增 `Diner.pending_allergy_terms`、`Diner.pending_allergy` 和解析协议 `DinerUpdate.allergy_clarifications`；个人待确认过敏字段是新增状态。部署前应备份 SQLite 数据库及相应镜像；若回退到不认识新字段的旧版本，应同时恢复匹配的数据快照，不能只切回旧代码。本轮未连接或迁移实际部署实例。
- 冷热判定仍是基于菜谱文本的有限启发式，不代表测量温度或对全部自然语言步骤的正确理解。未知保持未知。
- 新版评测器对登记预期与固定禁词做独立核验，不能据此宣称覆盖所有可能的过敏食材和自然语言表达。
- 本轮使用合成画像、MockTransport 和本地真实菜谱，不调用真实 DeepSeek；不产生新版模型完成率或公网延迟成绩。原 27/42 与当前修复后表现不能直接等同。
- SSE 仍在完整规划、校验与解释后开始分块；本次补齐菜单文本，不声称改善 TTFT。逐人定量营养、正式提交协议验收和有预算的真实模型回放仍属后续工作。

## 原始审查记录（保留历史证据）

下文完整保留修复前审查的事实、复现与当时结论；其中“当前 main”“仅审查”等表述均指审查基线 `ed79c5e`，不代表本轮维修结果。

---

# DietAgent PR #2–#5 审查报告

审查日期：2026-09-27（北京时间）  
仓库：https://github.com/CoirRaincoat/DietAgent  
固定审查提交：`ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc`  
比较基线：PR #1 合并提交 `d22a5fdecdc10b20c1b3caf46f6f6347c5dfdb19`

## 结论

这批 PR 的功能方向有价值，但当前 main 不宜直接作为新的已验收基线。本次确认 **7 项问题：4 项 P1、3 项 P2**。其中包括跨轮过敏约束丢失、正确个人过敏被误拦截、文本接口漏掉菜单，以及回归评测误判通过。

全部现有后端测试通过不能覆盖这些问题：本次运行 **385 passed，1 条既有依赖弃用警告**；Ruff 通过。补充反例采用真实业务代码、合成画像、MockTransport 或人工 Intent，不调用真实模型。这证明相应代码路径存在缺陷，不等于测得线上发生率，也不是新版真实模型完成率。

本次仅审查，没有修改仓库跟踪文件、合并/回退提交或发布 GitHub 评论。

## 审查范围与版本

上次 `SYNC_EVALUATION_REPORT.md` 的公开副本基线为 `d22a5fd`，只包含 PR #1。因此队友的 #2–#5 都是上次 27/42 评测之后的新改动；旧分数不能直接用于当前 main。

| PR | 内容 | 作者 | 当前状态 | 合并提交 |
|---|---|---|---|---|
| [#2](https://github.com/CoirRaincoat/DietAgent/pull/2) | OpenAI 兼容 Chat Completions 与 SSE | lcx123-code | 已合并 | 9964864 |
| [#3](https://github.com/CoirRaincoat/DietAgent/pull/3) | 多人身份、约束归属与逐人核验 | lcx123-code | 已合并 | b2e068a |
| [#4](https://github.com/CoirRaincoat/DietAgent/pull/4) | 菜单结构排序与事实说明 | lcx123-code | 已合并 | 0194b3f |
| [#5](https://github.com/CoirRaincoat/DietAgent/pull/5) | 合成回归、TTFT 与性能报告 | lcx123-code | 已合并 | ed79c5e |

审查时没有开放 PR。相对基线共 29 个文件变更，新增 3719 行、删除 52 行。GitHub Actions 查询返回 0 条运行，PR 描述中的测试数字属于提交者的验证记录，不能当作远端 CI 记录。

优先级：P1 表示进入下一轮候选验收前应修复；P2 表示有明确错误，应纳入同一修复阶段，但不建议因此整体撤销所有功能。

## F1 — P1：未知个人过敏原导致同轮已知过敏也丢失，下一轮能推荐禁忌菜

来源：PR #3。  
位置：[app/agent/service.py:146–154](https://github.com/CoirRaincoat/DietAgent/blob/ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc/app/agent/service.py#L146-L154)。

### 复现

1. 已建立“用户与爸爸”的两人晚餐会话，餐次等必要信息已确认。
2. 输入“爸爸对鸡蛋和神秘酱料过敏”，注入正确解析结果：
   `DinerUpdate(diner="爸爸", allergies=["鸡蛋", "神秘酱料"])`。
3. 当前轮返回要求澄清“神秘酱料”。
4. 下一轮输入“先推荐一份吧”，解析结果不撤销任何约束。

实际观察：

```text
首次：clarification_required
pending_allergy = False
pending_allergy_terms = []
爸爸 allergies = []

下一轮：ok
整桌 allergies = []
菜单 = 清炒西兰花、蒸鸡蛋、米饭
```

### 原因与影响

代码在发现任一未知个人过敏原时立即返回，尚未保存已知的“鸡蛋”，也未保存人物归属的待澄清项。下一轮覆盖旧澄清文本后，没有持久状态阻止规划，于是用户已经声明的禁忌食材重新进入菜单。

这是确定的跨轮硬约束漏检；不能只在首次响应中询问一次就视为处理完成。

### 修复与验收

先保存本轮能够确认的个人限制，并持久化人物归属的未决过敏信息。未明确解决前持续阻止规划；不能因下一轮未重复提到过敏就清除。测试至少覆盖“已知＋未知一起追加 → 首次澄清 → 无关追问 → 仍不能输出菜单 → 明确解决后保留已知限制”。

## F2 — P1：正确个人过敏被 DeepSeek 适配器强制改成澄清

来源：PR #3 的新协议与旧校验不兼容。  
位置：[app/infrastructure/llm/deepseek.py:148–156](https://github.com/CoirRaincoat/DietAgent/blob/ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc/app/infrastructure/llm/deepseek.py#L148-L156)，以及 [configs/intent_prompt.txt](https://github.com/CoirRaincoat/DietAgent/blob/ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc/configs/intent_prompt.txt#L26-L27)。

### 复现

使用真实 `DeepSeekLLM.parse`，仅用 `httpx.MockTransport` 返回合规模型 JSON：

```json
{
  "action": "plan",
  "people": 2,
  "meal_type": "晚餐",
  "diner_updates": [
    {"diner": "爸爸", "attendance": true, "allergies": ["花生"]}
  ]
}
```

用户文本为“我和爸爸两个人晚餐，爸爸花生过敏”。适配器输出变成：

```text
action = clarify
clarification = 你提到了过敏，请确认具体过敏食材后再规划菜单。
```

### 原因与影响

新提示词要求个人过敏放在 `diner_updates[].allergies`，不要重复写到顶层。适配器却仍只看 `intent.allergies`；于是模型完全按新要求提取，也会被旧保护逻辑误拦截。服务层已考虑个人过敏，适配器没有同步。

现有多人业务测试主要用 `ScriptedLLM` 直接返回 Intent，因此绕过真实适配器，没有发现这个衔接问题。

### 修复与验收

统一“本轮是否已明确过敏原”的判断，覆盖顶层、人物归属及保留既有信息等语义。补真实适配器与服务组合测试；不能要求模型在两个字段重复同一事实来规避错误。

## F3 — P1：Chat Completions 成功响应可能完全没有菜单菜名

来源：PR #2。  
位置：[app/api/openai_compat.py:181–205](https://github.com/CoirRaincoat/DietAgent/blob/ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc/app/api/openai_compat.py#L181-L205)。

### 复现

采用真实 FastAPI TestClient、合成菜谱和测试模型；让解释选择器仅返回合法事实 ID `["catalog"]`。分别请求非流式与流式 `/v1/chat/completions`。

| 观测 | 非流式 | SSE |
|---|---|---|
| HTTP 状态 | 200 | 200 |
| 后端有效菜单 | 清炒西兰花、蒸鸡蛋、米饭 | 相同 |
| 文本包含的上述菜名 | 0 个 | 0 个 |

响应只包含来源、约束、搭配数量及营养边界的通用说明。

### 原因与影响

两个渲染路径都只输出 `result.reason`，没有输出 `result.menu`。解释选择器可以省略所有单菜事实，因此不能依赖解释文字碰巧包含整份菜单。

这会导致接口“成功”，但评测端或纯文本客户端不知道推荐了什么。现有测试解释器返回所有事实，掩盖了缺陷。

### 修复与验收

从已验证的 `result.menu` 确定性渲染编号、菜名及需要的溯源信息，再附解释；JSON 与 SSE 共用该渲染器。验收应强制检查每一道最终菜单均出现在文本中，即使解释选择器省略全部单菜事实。

## F4 — P1：多人回归能把丢失硬约束的菜单判为全部通过

来源：PR #5。  
位置：[evaluation/cases/regression_v1.json:60–70](https://github.com/CoirRaincoat/DietAgent/blob/ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc/evaluation/cases/regression_v1.json#L60-L70)；[evaluation/regression_suite.py:229–241](https://github.com/CoirRaincoat/DietAgent/blob/ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc/evaluation/regression_suite.py#L229-L241)。

### 复现

场景原文要求：“我对海鲜过敏，小王不吃花生，小李不吃辣”。

构造响应时清空三人的过敏、忌口和不辣字段；保持人数、菜数等结构符合预期；菜单使用真实库中含虾的四道菜，保留真实来源信息；逐人核验字段均报告 `hard_constraints_satisfied=true`。

评测结果：**9/9 检查通过**。

### 原因与影响

该场景没有明确断言三种限制及人物归属。所谓硬约束通过，只检查被测服务自己的通过布尔值和 violations 列表。若解析阶段已经漏掉约束，后续程序对错误状态的自检也能全部通过。

这尤其影响下一阶段“修改—评测—接纳”的流程：分数可能奖励遗漏约束的版本。

### 修复与验收

在版本化用例中明确逐人预期事实和整桌聚合结果，并使用预期约束独立核对实际菜谱内容及替换建议。补负向评测器测试：主动删除或错配一个限制、插入违禁食材，评测必须失败。旧报告保留，新验证器应明确版本，不要悄悄覆盖旧成绩。

## F5 — P2：预处理冷藏/放凉被当成最终冷食

来源：PR #4。  
位置：[app/agent/menu_balance.py:29–36](https://github.com/CoirRaincoat/DietAgent/blob/ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc/app/agent/menu_balance.py#L29-L36)。

使用仓库真实 2000 条菜谱，调用现有标准化与冷热分类：

| 菜谱 | 真实步骤含义 | 实测分类 |
|---|---|---|
| 梅干菜扣肉（source_row 26） | 冷藏腌制后蒸制，末尾明确“趁热食用” | cold |
| 葱香土豆泥（source_row 187） | 晾凉后再炒，末尾明确“趁热食用” | cold |
| 烤火鸡腿（source_row 83） | 冷藏腌制后烘烤，末尾再烤10分钟 | cold |
| 蒸木耳素饺子（source_row 50） | 馅料放凉后包饺子并蒸制 | cold |

原因是全文出现“冷藏”“放凉”“晾凉”即返回 cold，优先于后续热加工证据。它既污染用户可见的“冷食几道”说明，也进入菜单排序，可能使全热菜单被认为已具备冷热搭配。

建议识别最终出餐或明确冷食证据，处理步骤顺序；不能可靠确定时保持未知。用上述真实菜谱补回归，不要仅用自造“凉拌黄瓜”正例。

## F6 — P2：旧会话更新人数时丢失原定菜数

来源：PR #3。  
位置：[app/agent/service.py:285–291](https://github.com/CoirRaincoat/DietAgent/blob/ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc/app/agent/service.py#L285-L291)，触发处 [231–232](https://github.com/CoirRaincoat/DietAgent/blob/ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc/app/agent/service.py#L231-L232)。

复现升级前会话：两人晚餐，明确“四菜一汤”，内部 `dish_count=5, soup_count=1`。升级后输入“改成3个人，其他照旧”，正确 Intent 只修改人数。

实测菜数变为 `dish_count=4, soup_count=1`。

旧快照没有新字段 `menu_structure_explicit`，加载后默认 False。迁移没有保守保护既有结构，人数变化于是触发默认菜单重设。能反序列化旧会话不等于业务兼容。

建议迁移时保护旧的菜数和汤数；不能确定来源的旧值不应直接视为“用户没指定”。加入真实旧格式快照与人数/参餐变化的跨版本回归。

## F7 — P2：性能请求失败时，摘要仍可能给性能满分

来源：PR #5。  
位置：[evaluation/regression_suite.py:585–589](https://github.com/CoirRaincoat/DietAgent/blob/ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc/evaluation/regression_suite.py#L585-L589)，以及 [644–659](https://github.com/CoirRaincoat/DietAgent/blob/ed79c5e8abaf0f0530f877bf6e24e0d2c2fb1bbc/evaluation/regression_suite.py#L644-L659)。

模拟默认性能子集的 4 次请求：前 3 次成功（TTFT=100ms、端到端=200ms），第 4 次 HTTP 503（耗时90秒）。

实际：

- `threshold_result_valid=false`。
- 成功请求计算的三个延迟档位仍是 excellent。
- `summarize_run` 仍给性能 30/30；若功能场景通过，总内部诊断分可为100。
- Markdown 性能表只展示优秀档位，没有显式展示无效状态和失败数量。

JSON 留存了失败；CLI 末尾也会因无效性能返回失败退出码。**不能因此声称 CI 会通过**。缺陷在于供人工阅读、贴到 PR 中的摘要和评分可能误导版本判断。

建议无效性能不参与接纳评分，显式展示成功/失败/未执行数与有效性；成功请求耗时可以保留用于诊断，但不能标为整组有效达标结果。

## 已确认的进步与边界

可以保留的工作：

- 建立了人物身份、约束归属和共享菜单聚合结构。
- 新增菜单类别/做法搭配排序，并避免宣称缺乏数据支持的定量营养。
- 新增 SSE、Server-Timing、版本化用例及逐轮结果记录。
- 代码采用真实库内菜谱和结构化规则，未因这些改动引入模型自由生成营养数值。

仍需明确的限制：

- SSE 是完成整份规划与解释后再分块，尚不能据此声称首次有效内容更快。
- Chat Completions 只接受单条增量 user 消息，并不是完整历史 messages 的通用兼容端点。这是已声明的范围，需要核对正式接入协议。
- 逐人“已知约束核验”不等于逐人份量和营养摄入计算。
- 新合成回归集是补充开发回归，不替代原20场景42轮，也不能当成独立盲测。
- 本次没有进行真实 DeepSeek 质量回放、Docker部署验收或公网性能测试。

## 验证记录

环境：Python 3.12，独立临时环境，项目 requirements.lock；额外安装 socksio 仅用于支持当前执行环境的代理配置，不修改项目依赖文件。

首次完整测试为384通过、1失败，失败发生在构造httpx客户端时缺少环境代理所需的socksio，尚未进入模型网络调用。补齐环境依赖后重新运行：

```text
python -m pytest -q
385 passed, 1 warning in 5.81s

ruff check app pipelines evaluation tests
All checks passed!

git diff --exit-code
通过；无跟踪文件改动。
```

补充验证包括：真实适配器＋MockTransport、MealAgent＋SQLite跨轮状态、FastAPI TestClient的两种接口响应、真实CSV冷热反例，以及向评测器注入已知错误输出。使用合成数据，无真实模型付费调用。

## 建议修复顺序

1. **先修 F1、F2、F3。**保证硬约束持久化、正常个人过敏可以完成请求、文本接口确实返回菜单。
2. **随后修 F4、F7。**让回归判断与性能摘要能够支撑候选版本选择。
3. **补 F5、F6。**校正冷热说明和排序依据，保护旧会话语义。
4. 在固定提交上重跑现有测试与这些反例；随后另行安排有预算的原42轮真实模型对照和正式入口验收。

建议保留功能提交，在小范围修复分支分别补缺陷与测试。当前无需因为这些问题整体回退所有PR，但在上述关键问题修好前，不应把 main 标为新的验收通过版本。

