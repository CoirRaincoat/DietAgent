# 合成回归集与运行报告

私有的 50 份档案 × 20 组原始对话回放见 [私有矩阵文档](PRIVATE_MATRIX.md)。它复用本报告的 JSON、Markdown、JSONL 写入层，并采用脱敏字段白名单。

## 目的与边界

默认测试集为 `evaluation/cases/regression_v3.json`，当前验证器为 `synthetic-validator-v19`，报告格式为 `2.1`。它是可公开提交、可版本化的合成开发回归集。它只使用手写合成画像 `900001`、`900002`、`900003`，不包含正式验收样例、真实健康档案或真实用户对话。

2026-10-01 v19新增可选 `expect.cooking_methods`：`reviewed_methods` 是另读源后手写的源ID到唯一方法字符串数组映射，空数组为做法未建立，不是没有烹饪或完美新颖。菜单/全部建议须有审核项及源目录观察；缺失、畸形、不符使 `independent_cooking_methods` 失败并阻止质量观察。期望不能由生产解析器、目录/响应标签或成功标志自证；安全、角色和来源身份另验。本轮源方法v3、质量v5，公共v3原字节不改，原失败报告保留，见 [成菜做法源证据及负结果](FINISHING_METHOD_EVIDENCE.md)。

2026-10-01 v18新增可选 `expect.culinary_focus`：`reviewed_focus` 为另行读源后编写的源ID到唯一家族字符串数组映射，空数组表示该有限排名口径未建立主体证据，不是食物不存在或完美新颖。菜单及所有建议须有审核项和被测目录观察；缺失、畸形或不符使 `independent_culinary_focus` 失败，并阻止质量观察。不导入生产主体模块来生成期望，不能从响应自报或成功标志自证；来源真实性/角色/正餐资格/安全门禁另验。这不是主料占比、整体质量分或人工盲评。v3原始数据不改，旧v17报告保留，见 [主体实验与负结果](CULINARY_FOCUS_EXPERIMENT.md)。

2026-10-01 v17新增可选 `expect.primary_roles`：`reviewed_roles` 为另行审核的源ID到protein/vegetable/staple/soup映射，不能从生产分类器生成。菜单和所有建议需有审核及被测目录角色观察，不接受响应类别/成功标志自证；缺失、无效或不符使 `independent_primary_roles` 失败，阻止质量观察。必须另配来源身份、正餐资格与安全门禁；角色核对不证明营养量或全库准确率。公开v3原字节保持不变，旧v16报告不改写。见 [包馅角色复核](WRAPPED_DISH_ROLE_FIX.md)。

测试集当前包含 30 个独立场景，按 `8 基础 + 8 复杂 + 14 交互` 分层。全量功能回放覆盖全部 30 组，标记的性能子集共 10 个请求。主要覆盖：

- 早餐/午餐/晚餐结构、菜数/汤数、显式忌口、画像过敏与健康目标；
- 2–5 人场景中的限制分别归因、多人聚合、宴请结构、出席变化与未知过敏原消歧；
- 逐项澄清、约束与偏好追加、第一/第二/第三道局部替换、连续整份否定、上下文解释和跨轮未决状态。

功能检查读取 `/chat` 的结构化响应，验证 `recipe_id` 与来源、会话约束、逐人硬约束、营养结果与菜单 ID 对齐、换菜槽位和拒绝记忆。v2 增加以下独立检查：

- `expected_diners` 按档案本人或明确姓名断言人物事实，检查限制的正确归属及不应出现在其他人身上的限制；整桌 `constraints` 单独断言预期聚合值。
- `catalog_traceability` 用本地菜谱 CSV 核对菜单及替换建议的 ID、名称、来源行和指纹，缺少来源或不匹配时失败。不能将服务自报的来源当作独立证据。
- `independent_food_rules` 使用用例内固定禁词检查真实源食材、烹饪步骤及响应食材/步骤，覆盖菜单与替换建议。它不调用被测 `RuleEngine`，不从返回的约束推导预期，也不因服务自报“通过”就放行。
- v3 验证器对每轮 `reason` 增加确定性文案门禁：要求正文存在且长度合理，不暴露 `recipe_id` 等内部术语，不重复营养局限；局部替换、整桌重排和上下文解释还要明确回应相应意图。该门禁只检测可客观判定的回归，不替代专家对自然度和搭配质量的评价。
- v4 验证器增加**不计分**的菜单质量观察。仅当成功菜单的本地菜谱来源、独立食材规则和已声明的硬约束检查通过时，才根据源菜谱计算。缺少这些核验、核验失败或源菜谱不可用时记 `unavailable`，不把未知值记作零。
- v5 验证器增加 `independent_food_requirements` 正向源证据。对于“希望这餐有牛肉”等偏好，它要求主菜单至少一道真实源菜谱的原料或步骤命中用例内固定必含词；不接受仅在会话状态或回复文案中自报“已满足”。
- v6 验证器为用例明确要求不辣的轮次补充 `non-spicy-source-oracle-v1`：覆盖小米辣、油泼辣子等反例、显式辣味标签及成分不明调料，同时审核菜单与替换建议的源配方和可见输出。不从服务自报约束或生产词表获取预期；旧 v3 数据集字节与历史报告保持原样。新旧验证器结果不能混为同一通过率，修复及反例集见 [不辣修复记录](NON_SPICY_FIX.md)。
- v7 验证器增加 `main_meal_eligibility` 与独立 `main-meal-counterexample-oracle-v1`，检查菜单及建议是否命中已知饮料、甜品、甜汤或加工记录。它不导入生产角色分类器；命中反例时菜单质量观察不可用。有限集合不证明全部角色正确或营养均衡；数据集字节与历史报告不变，详见 [正餐资格修复记录](MAIN_MEAL_ROLE_FIX.md)。
- v8–v11 在本地结构回放中迭代补充有限源反例；当前oracle v5另读步骤，覆盖甘蔗马蹄水、甜糕、荔枝饮品/梨挞、准备-only汽锅鸡及未烹饪虾滑。完整同名变体不凭名称拒绝；原始阶段报告保留，最新oracle重审另列，不回写旧绿灯。数据集v3字节不变；详见 [多人结构修复与边界](MEAL_STRUCTURE_FIX.md)。多人角色默认本身不是公共回归独立质量分，私有矩阵尚未补同一正餐oracle，不混用通过率。

这些词表只覆盖声明的有限开发回归事实，不代表完整医学、过敏原或营养验证；扩充词表也属于用例版本变更。本地源菜谱必须与服务使用的数据版本一致；报告记录 CSV SHA-256，源不匹配的结果不能用于接纳。旧 `regression_v1.json`、`regression_v2.json` 和旧报告保持原样；可用 `--suite` 显式复跑旧版本，但旧版本与 v3 的覆盖和哈希不同，结果不能直接横比。

v12 可选 `expect.dish_composition` 增加开发者源审核表的荤素数量验收；v13 可选 `expect.whole_meal_diet` 增加**整餐**审核：`mode` 为 `ovo_lacto_vegetarian` 或 `vegan`，`reviewed_source_kinds` 为源ID到 `plant`、`egg_dairy_honey`、`meat`、`unknown` 的独立审核表。蛋奶素允许前两类，纯素仅允许plant；每道汤、主食及全部建议都检查，空菜单/未知ID/未知来源失败。不导入生产饮食分类器、不采信返回标签；应由同一CSV原料/步骤编写，并结合 `catalog_traceability`，不是盲评标签。新门禁失败会令菜单质量观察不可用，不把拒绝输出算成功。公共v3原字节保留，未为它伪造审核表。源正餐反例oracle升v7，补银耳蛋汤显式冰糖的甜汤对照；旧结果不重写。见 [整餐饮食模式记录](WHOLE_MEAL_DIET_FIX.md)。

标记了 `measure_performance` 的场景会再次通过 `/v1/chat/completions` SSE 运行，用客户端边界测量 TTFT 与完整耗时。

## 每次测试生成什么

每次运行在 `runtime/regression_reports/<UTC时间>/` 创建独立目录：

| 文件 | 用途 |
| --- | --- |
| `report.json` | 数据集版本/哈希、Git 提交、逐场景逐断言、服务端阶段耗时、TTFT/E2E 和汇总 |
| `report.md` | 便于人工阅读和放入 PR 描述的摘要 |
| `responses.jsonl` | 合成场景的逐轮完整结构化响应，便于定位失败 |

`runtime/` 已加入 `.gitignore`，不会误把运行报告或未来的本地数据提交到仓库。若需要在 PR 中展示结果，复制 `report.md` 的摘要即可；不要提交正式测试集或真实用户响应。

报告记录数据集版本、验证器版本、数据集与菜谱 CSV SHA-256；对照时应固定这些输入和运行条件。新报告标记为 `synthetic_regression_evidence_not_official`，不生成质量总分。旧报告中的诊断分仅供历史追溯，不应与新证据卡当作同一指标比较。

`report.json` 的 `cases[].turns[].menu_quality` 和 `summary.menu_quality`、`report.md` 的“菜单质量观察”当前采用 `source-menu-diversity-v5` 口径（主角色v4，准备证据v2，成菜方法v3）。v2开始源归一化类别互斥；旧v1可一菜多标签，后续有限规则继续变更，跨口径类别计数不能直接横比，历史文件不改写。旧v4全方法标签数不可直接与新成菜方法数比较为优化增益。分类仍是启发式，不表示纯素、健康或营养量，见 [主角色修复](PRIMARY_DISH_ROLE_FIX.md)、[健康/新源反例记录](HEALTH_PREFERENCE_FIX.md)、[成菜方法](FINISHING_METHOD_EVIDENCE.md)。每份可核验菜单记录：

- `category_counts`：源菜谱的蔬菜、蛋白质来源、主食、汤类标签计数；`role_coverage` 为前三类中出现的类别数（0–3），不是荤素比或营养比例。
- `method_count`：不同的源成菜方法种数，不计器具/准备/浇头；`method_evidence_version`、`method_known_dishes` / `method_unknown_dishes` 披露口径及已知/未知覆盖，汇总 `method_coverage_menus` 只含同版有覆盖字段的菜单，旧数据缺字段保持未测而非补0。`temperature_counts` 仍是菜名/步骤的有限冷热文字证据，含 `unknown`；不是实际出餐温度或烹饪验证。
- `ingredient_overlap_mean` / `ingredient_overlap_max`：菜单菜品两两原料名称集合的 Jaccard 重合度均值与最大值。仅比较有原料名称的菜品对，按原文规范化后的名称精确匹配，包含调味料；不推断别名或份量。单菜或没有可比较菜品对时为 JSON `null`，不是 0。

汇总中的均值以**菜单**为单位等权计算，`menus_measured` 是可计算菜单数，`menus_unavailable` 是成功菜单中因核验或数据缺失而不可计算的数量。非成功回复和 HTTP 失败不进入菜单质量均值，仍按原有功能检查计失败。上述观察值不产生分数或正式验收结论；不能用它们宣称逐人定量营养均衡或真实上菜冷热比。旧 v1 测试集缺少独立源核验时会显示 `validation_not_observed`，不能与 v2/v3 质量观测直接对比。

- `rubric_results` 分别记录基础/复杂/交互的已测、通过、失败用例数；不换算为质量分。
- `performance.counts` 展示计划请求数 `scheduled`、已执行 `requests`、成功 `successful`、失败 `failed` 和因前轮失败等原因未执行的 `not_executed`。
- 任何失败、未执行或缺少必要指标都会使性能结果无效。`performance_status` 为 `invalid`；跳过性能时为 `not_run`。
- 成功请求的延迟分布保留作诊断；无效组 `status`/`status_by_mean` 为 `invalid`，成功子样本档位单列在 `successful_only_status`/`successful_only_status_by_mean`。无论性能档位如何，都不把有限性能样本换算为整组质量分。
- `quality_score_status=unscored_unvalidated`：目前没有经独立校准的综合质量分；合成场景全过也不会生成 100 分。

CLI 的 `--skip-performance` 可用于功能调试并依据功能结果退出，但不会产生性能达标结论。

## Docker 运行

先启动待测分支：

```powershell
$env:DEMO_PORT="8081"
docker compose --project-name dietagent-pr15 --env-file configs/compose.env --file docker-compose.yml `
  up --build --detach
```

运行完整合成回归和性能子集，并把报告直接写回当前工作区：

```powershell
docker compose --project-name dietagent-pr15 --env-file configs/compose.env --file docker-compose.yml `
  run --rm -v "${PWD}:/work" -w /work backend `
  python -m evaluation.regression_suite `
  --base-url http://frontend:8080 `
  --output-root /work/runtime/regression_reports
```

功能调试时可临时跳过第二次 SSE 性能回放：

```powershell
docker compose --project-name dietagent-pr15 --env-file configs/compose.env --file docker-compose.yml `
  run --rm -v "${PWD}:/work" -w /work backend `
  python -m evaluation.regression_suite `
  --base-url http://frontend:8080 `
  --output-root /work/runtime/regression_reports `
  --skip-performance
```

即使断言失败，三份报告也会先写入目录；进程随后返回非零退出码，适合作为 CI 或 PR 的质量门禁。性能超过合格阈值、HTTP/SSE 请求失败或流缺少非空正文/`[DONE]` 同样返回非零。

## 数据集升级规则

2026-10-01 有限正餐源oracle升v11，追加蜂蜜桂花酱刷南瓜、明确蘸红糖山药、冰糖银耳西瓜盅的另行源反例，保留同名不同咸味配方的对照。准备证据为v4；公开验证器仍v16、原始v3数据字节不变。235项开发/源输入不是独立留出：有限正餐资格改善同时发现荠菜包建议主角色错误，报告单列失败，不合成质量满分。见 [甜点式准备复核记录](SWEET_ROOT_PREPARATION_FIX.md)。下文v9为上一阶段记录，旧报告不改写。

2026-10-01 验证器v16新增可选 `expect.health_reporting`：`goals` 是唯一目标数组，`reviewed_goals` 是源ID到逐目标 `{configured: bool, cautions: [源关注词]}` 的另行审核表。全部菜单与建议需有审核项；已核对关注词必须在健康配料/理由中展示并标 `caution`，未配置目标只能 `insufficient_data`，缺少范围说明或显式虚假达标声明失败。它不导入生产健康评分，必须另配源真实性检查；空关注表不是低钠/低糖证明。失败令质量观察不可用。不是AI评分或人工盲评标签，v3公开集原字节不变。有限正餐oracle升v9，会标记本轮仍未修的南瓜甜菜反例；不为获得绿灯弱化它。详见 [健康复核记录](HEALTH_PREFERENCE_FIX.md)。

2026-10-01 验证器v15/源反例oracle v8曾补充五类准备反例；生产主角色与菜单观察当时分别升v3。旧v2/v3数值和报告不改写，跨口径不可直接横比。源步骤欠缺、未知字段不得默认完成；设备指令没写“煮”不能直接拒绝。专项与正向误拒证据见 [准备过程修复](PREPARATION_EVIDENCE_FIX.md)。公开v3场景原始字节不变，不将开发源回放称作独立留出或官方数据。

2026-10-01 验证器v14支持可选 `expect.meal_context`：`meal_type` 为确切餐次；`reviewed_context` 为开发者另行审核的ID映射，每项 `audience` 是 `ordinary` / `infant_only`，`meals` 是确切源餐次数组。默认 `require_matching_tags=true`，缺失/其他餐次证据、婴儿专用或未审核ID均失败，菜单与建议同时核对；false只验普通人群，不能当作已验证餐次适配。需配合源真实性检查，失败阻止质量观察。不是临床适配、人工盲评标签或全库准确率；不改写公开v3原始数据。新增实际源回放仍发现甜品及步骤不完整，见 [本轮报告](MEAL_CONTEXT_FIX.md)。

2026-10-01 验证器v12增加可选 `expect.dish_composition`：`meat` / `vegetarian` 为明确非汤、非主食成菜数量；`reviewed_recipe_kinds` 为针对同一菜谱源逐条审核的ID到 `meat` / `vegetarian` / `soup` / `other` 映射。审核表不得从生产分类器或服务自报生成，也不是人工盲评标签；缺失记录使验收失败。核对菜单及每个第一槽替换建议，失败时不生成菜单质量观察；需要结合 `catalog_traceability`、唯一ID、总数/汤数与正餐资格检查。源正餐反例oracle升v6，既有开发v3文件与历史报告不改写。此口径不保证整餐素食、份量或临床效果，见 [数量复核记录](DISH_COMPOSITION_FIX.md)。

不要静默修改既有版本含义。新增或调整消息、期望和评分覆盖时：

1. 新建下一版本文件，例如 `regression_v3.json`；
2. 更新 `dataset_version`；
3. 保留旧版本，便于历史报告复现；
4. 只使用合成资料，不把正式样例改写后公开；
5. 对运行器新增检查时同时补单元测试。
