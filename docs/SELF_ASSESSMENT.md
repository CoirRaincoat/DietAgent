# 一体化自测与证据卡

## 为什么增加这一层

`evaluation.regression_suite` 报告基础推荐、复杂组合和多轮交互的**用例数与通过数**，并独立记录性能分布与菜单质量观察。一体化证据卡检查门禁，但不把少量已知合成用例的通过率换算为题面 100 分，解决以下问题：

- 局部成功请求不能掩盖性能请求失败或未执行；
- 一个看似较高的总分不能掩盖过敏、忌口或菜谱来源失败；
- 没有运行某项检查时，不能默认当作通过；
- 私有矩阵覆盖与真实模型回归必须分开解释。

证据卡包含以下必需门禁：

| 门禁 | 通过条件 |
|---|---|
| `functional_regression` | 声明的公开回归场景全部执行且通过 |
| `hard_constraint_safety` | 已实际观察到硬约束核验，且无失败 |
| `recipe_traceability` | 已实际观察到菜谱来源核验，且无失败 |
| `performance` | 所有计划请求完成，TTFT、单轮和多轮指标有效且至少合格 |
| `private_matrix` | 仅在提供私有数据时成为必需；覆盖完整且无失败代码 |

门禁结果有三个状态：

- `pass`：已配置的必需回归证据完整且通过，不代表推荐质量满分；
- `fail`：存在已确认失败；
- `incomplete`：关键检查没有运行。

`assessment.json` 的 `quality_score_status` 固定为 `unscored_unvalidated`，`validated_score` 为 JSON `null`，`score_valid=false`。即使所有门禁通过，也只表示这些用例上的回归结果；没有独立质量标注、逐人营养核验和专家评审时，不生成推荐质量总分。旧报告中的 `diagnostic_score=100` 不会被提升为有效质量分。机器可读的分项用例计数见 `rubric_results`。

## 一条命令运行

在仓库根目录执行：

```powershell
.\scripts\run_self_assessment.ps1
```

脚本默认读取当前工作树中被 Git 忽略的 `.env`。如果模型配置保存在另一份工作树，显式传入该文件；其中允许的模型配置会覆盖当前 PowerShell 中可能过期的同名变量，值不会输出：

```powershell
.\scripts\run_self_assessment.ps1 `
  -SettingsFile "D:\Projects\DietAgent\.env"
```

默认流程依次执行：

1. 检查 Docker Engine；
2. 构建并启动当前分支；
3. 等待 `/health`，并要求 `llm_configured=true`；
4. 在后端容器运行完整 pytest；
5. 运行 10 组公开合成回归及 SSE 性能子集；
6. 汇总严格门禁、分项用例结果和菜单质量观察；不生成未经校准的总分。

加入本地私有矩阵：

```powershell
.\scripts\run_self_assessment.ps1 `
  -SettingsFile "D:\Projects\DietAgent\.env" `
  -PrivateDataDir "C:\Users\jack\OneDrive\桌面\数据支持方太"
```

私有目录需要两个 JSON 文件和菜谱 CSV。脚本按结构自动识别 20 项对话文件；存在完整与脱敏两份 50 项档案时，默认选取内容更丰富、文件更大的档案。菜谱默认文件名为 `recipes_sample_2000.csv`。也可以显式覆盖：

```powershell
.\scripts\run_self_assessment.ps1 `
  -PrivateDataDir "C:\path\to\authorized-data" `
  -ProfilesFile "profile-file.json" `
  -DialoguesFile "dialogue-file.json" `
  -RecipesFile "recipes_sample_2000.csv"
```

宿主脚本本身保持纯 ASCII，兼容默认按系统代码页读取脚本的 Windows PowerShell 5；中文文件名来自目录扫描或命令行参数，不再硬编码在 `.ps1` 中。

常用调试参数：

```powershell
# 已有最新镜像时跳过构建
.\scripts\run_self_assessment.ps1 -SkipBuild

# 只调试功能；证据卡会把性能标为 not_run，门禁状态 incomplete
.\scripts\run_self_assessment.ps1 -SkipPerformance

# 仅在已单独运行完整测试时使用
.\scripts\run_self_assessment.ps1 -SkipUnitTests
```

## 输出目录

每次运行使用独立目录：

```text
runtime/self_assessments/<UTC时间>-<随机后缀>/
├── assessment.json
├── assessment.md
├── regression/<UTC时间>/
│   ├── report.json
│   ├── report.md
│   └── responses.jsonl
└── private/<UTC时间>-<随机后缀>/       # 提供私有目录时存在
    ├── report.json
    ├── report.md
    ├── failures.jsonl
    └── sessions.sqlite3
```

建议首先打开 `assessment.md`。门禁失败后，再进入对应的 `regression/report.md`、`responses.jsonl` 或 `private/failures.jsonl` 定位原因。`runtime/` 已被 Git 忽略；不要把私有报告、会话数据库或原始输入加入提交。

合成回归的 `regression/report.md` 另列“菜单质量观察（不计分）”及逐轮记录，可用于记录优化前基线。固定数据集哈希、源菜谱哈希、模型配置和运行环境后，再比较类别覆盖、做法种数、冷热证据与食材重合度。该部分目前只覆盖**公开合成回归**，不代表私有 50×20 矩阵或官方专家评分；观察值不会被强行折算成总分。

## 退出码

- `0`：所有已要求的步骤和质量门禁通过；
- `1`：单元测试、回归、私有矩阵或证据门禁失败/不完整。

脚本在回归或矩阵失败时仍继续生成最终证据卡，确保失败证据不会丢失。服务默认保留运行，便于复查；完成后可执行：

```powershell
docker compose --project-name dietagent-self-assessment `
  --env-file configs/compose.env --file docker-compose.yml stop
```

## 能力边界

- 公开合成回归会通过 HTTP 调用当前配置的模型、检索、规则和规划链路；它不是隐藏测试集。
- 合成回归会自动检查回复是否包含内部术语、重复局限说明及意图不匹配文案；这些是确定性质量门禁，不是主观自然度评分。
- 私有 50×20 矩阵使用人工审核的 Intent fixture，验证后端规则、规划和会话隔离，不测模型自然语言理解。
- 专家评审中的搭配自然度、解释质量仍需人工盲评。
- 缺少可靠份量与营养真值时，不能声称逐人定量营养达标。
