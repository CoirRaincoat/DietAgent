# 合成回归集与运行报告

私有的 50 份档案 × 20 组原始对话回放见 [私有矩阵文档](PRIVATE_MATRIX.md)。它复用本报告的 JSON、Markdown、JSONL 写入层，并采用脱敏字段白名单。

## 目的与边界

默认测试集为 `evaluation/cases/regression_v2.json`，验证器为 `synthetic-validator-v3`，报告格式为 `2.0`。它是可公开提交、可版本化的合成开发回归集。它只使用手写合成画像 `900001`、`900002`、`900003`，不包含正式验收样例、真实健康档案或真实用户对话。

测试集当前包含 10 个独立场景，覆盖：

- 基础单餐推荐、档案过敏与菜谱真实性；
- 多人分别归因的过敏/忌口、宴请菜数与汤数；
- 主动澄清、约束追加、局部替换、整份否定、上下文解释和模糊需求。

功能检查读取 `/chat` 的结构化响应，验证 `recipe_id` 与来源、会话约束、逐人硬约束、营养结果与菜单 ID 对齐、换菜槽位和拒绝记忆。v2 增加以下独立检查：

- `expected_diners` 按档案本人或明确姓名断言人物事实，检查限制的正确归属及不应出现在其他人身上的限制；整桌 `constraints` 单独断言预期聚合值。
- `catalog_traceability` 用本地菜谱 CSV 核对菜单及替换建议的 ID、名称、来源行和指纹，缺少来源或不匹配时失败。不能将服务自报的来源当作独立证据。
- `independent_food_rules` 使用用例内固定禁词检查真实源食材、烹饪步骤及响应食材/步骤，覆盖菜单与替换建议。它不调用被测 `RuleEngine`，不从返回的约束推导预期，也不因服务自报“通过”就放行。
- v3 验证器对每轮 `reason` 增加确定性文案门禁：要求正文存在且长度合理，不暴露 `recipe_id` 等内部术语，不重复营养局限；局部替换、整桌重排和上下文解释还要明确回应相应意图。该门禁只检测可客观判定的回归，不替代专家对自然度和搭配质量的评价。

这些词表只覆盖声明的有限开发回归事实，不代表完整医学或过敏原验证；扩充词表也属于用例版本变更。本地源菜谱必须与服务使用的数据版本一致；报告记录 CSV SHA-256，源不匹配的结果不能用于接纳。旧 `regression_v1.json` 和旧报告保持原样；显式传入 `--suite evaluation/cases/regression_v1.json` 可以运行旧断言，但它没有 v2 的独立核验能力，也不能把 v2 分数直接与 v1 分数比较。

标记了 `measure_performance` 的场景会再次通过 `/v1/chat/completions` SSE 运行，用客户端边界测量 TTFT 与完整耗时。

## 每次测试生成什么

每次运行在 `runtime/regression_reports/<UTC时间>/` 创建独立目录：

| 文件 | 用途 |
| --- | --- |
| `report.json` | 数据集版本/哈希、Git 提交、逐场景逐断言、服务端阶段耗时、TTFT/E2E 和汇总 |
| `report.md` | 便于人工阅读和放入 PR 描述的摘要 |
| `responses.jsonl` | 合成场景的逐轮完整结构化响应，便于定位失败 |

`runtime/` 已加入 `.gitignore`，不会误把运行报告或未来的本地数据提交到仓库。若需要在 PR 中展示结果，复制 `report.md` 的摘要即可；不要提交正式测试集或真实用户响应。

报告记录数据集版本、验证器版本、数据集与菜谱 CSV SHA-256；对照时应固定这些输入和运行条件。报告中的分数明确标记为 `internal_diagnostic_not_official`，不是评委分数。

- `functional_score` 是基础/复杂/交互三项的内部诊断分，满分 70。
- `performance.counts` 展示计划请求数 `scheduled`、已执行 `requests`、成功 `successful`、失败 `failed` 和因前轮失败等原因未执行的 `not_executed`。
- 任何失败、未执行或缺少必要指标都会使性能结果无效。`performance_status` 为 `invalid`；跳过性能时为 `not_run`。
- 性能无效或未执行时，`rubric_scores.performance` 和 `diagnostic_score` 都为 JSON `null`，`diagnostic_score_valid=false`，Markdown 显示不计分/未生成。不能把成功请求的低延迟折算成整组 30 分。
- 成功请求的延迟分布保留作诊断；无效组 `status`/`status_by_mean` 为 `invalid`，成功子样本档位单列在 `successful_only_status`/`successful_only_status_by_mean`。只有全部计划请求成功、三个指标有效时才计算 20/20/30/30 权重的 100 分诊断总分。

CLI 的 `--skip-performance` 可用于功能调试并依据功能结果退出，但不会产生有效总分或性能达标结论。

## Docker 运行

先启动待测分支：

```powershell
$env:DEMO_PORT="8081"
docker compose --project-name dietagent-pr5 --env-file configs/compose.env --file docker-compose.yml `
  up --build --detach
```

运行完整合成回归和性能子集，并把报告直接写回当前工作区：

```powershell
docker compose --project-name dietagent-pr5 --env-file configs/compose.env --file docker-compose.yml `
  run --rm -v "${PWD}:/work" -w /work backend `
  python -m evaluation.regression_suite `
  --base-url http://frontend:8080 `
  --output-root /work/runtime/regression_reports
```

功能调试时可临时跳过第二次 SSE 性能回放：

```powershell
docker compose --project-name dietagent-pr5 --env-file configs/compose.env --file docker-compose.yml `
  run --rm -v "${PWD}:/work" -w /work backend `
  python -m evaluation.regression_suite `
  --base-url http://frontend:8080 `
  --output-root /work/runtime/regression_reports `
  --skip-performance
```

即使断言失败，三份报告也会先写入目录；进程随后返回非零退出码，适合作为 CI 或 PR 的质量门禁。性能超过合格阈值、HTTP/SSE 请求失败或流缺少非空正文/`[DONE]` 同样返回非零。

## 数据集升级规则

不要静默修改既有版本含义。新增或调整消息、期望和评分覆盖时：

1. 新建下一版本文件，例如 `regression_v3.json`；
2. 更新 `dataset_version`；
3. 保留旧版本，便于历史报告复现；
4. 只使用合成资料，不把正式样例改写后公开；
5. 对运行器新增检查时同时补单元测试。
