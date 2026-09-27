# 一体化自测与严格评分卡

## 为什么增加这一层

`evaluation.regression_suite` 继续按照题面权重生成基础推荐 20 分、复杂组合 20 分、多轮交互 30 分和性能 30 分的内部诊断结果。一体化评分卡不修改这些原始分值，而是补充证据门禁，解决以下问题：

- 局部成功请求不能掩盖性能请求失败或未执行；
- 一个看似较高的总分不能掩盖过敏、忌口或菜谱来源失败；
- 没有运行某项检查时，不能默认当作通过；
- 私有矩阵覆盖与真实模型回归必须分开解释。

评分卡包含以下必需门禁：

| 门禁 | 通过条件 |
|---|---|
| `functional_regression` | 声明的公开回归场景全部执行且通过 |
| `hard_constraint_safety` | 已实际观察到硬约束核验，且无失败 |
| `recipe_traceability` | 已实际观察到菜谱来源核验，且无失败 |
| `performance` | 所有计划请求完成，TTFT、单轮和多轮指标有效且至少合格 |
| `private_matrix` | 仅在提供私有数据时成为必需；覆盖完整且无失败代码 |

门禁结果有三个状态：

- `pass`：证据完整且满足内部接纳条件；
- `fail`：存在已确认失败；
- `incomplete`：关键检查没有运行，不能形成有效总分。

`observed_diagnostic_score` 保留原始观测结果。只有全部必需门禁通过时，才把它复制到 `validated_score`；否则 `validated_score` 为 JSON `null`。两者都明确标记为 `internal_diagnostic_not_official`，不是评委官方成绩。

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
6. 汇总严格门禁和内部诊断分。

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

# 只调试功能；评分卡会把性能标为 not_run，总分无效
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

## 退出码

- `0`：所有已要求的步骤和质量门禁通过；
- `1`：单元测试、回归、私有矩阵或评分门禁失败/不完整。

脚本在回归或矩阵失败时仍继续生成最终评分卡，确保失败证据不会丢失。服务默认保留运行，便于复查；完成后可执行：

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
