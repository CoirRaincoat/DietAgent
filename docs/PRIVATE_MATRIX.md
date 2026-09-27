# 私有档案 × 原始对话矩阵回放

## 输入与解释

本入口读取本地原始 50 份健康档案、20 组对话和 2000 条菜谱。对话源文件只有 `id`、`turn_count`、`user_messages`，没有 `user_id` 或对话—档案映射。因此运行器对每个档案 ID 回放全部 20 组对话：共 1000 个独立会话、1450 个用户轮次。报告标记为 `matrix_without_source_binding`；矩阵是覆盖测试，不能解释为官方指定的 1000 组配对。

意图由 `evaluation.offline` 中对应源文件 SHA-256 的人工标注 fixture 提供。真实 Agent、菜谱检索、规则、规划与 SQLite 会话仍会运行。对话文件改变时运行器拒绝执行，需人工重新审核并版本化意图标注。此回放不测自然语言理解、真实模型首 Token 延迟或临床营养效果。

## 本地运行

在 PR6 工作区激活已安装项目依赖的 Python 3.11 环境，再传入已授权私有文件所在目录：

```powershell
$dataDir = "C:\path\to\authorized-data"
python -m evaluation.private_matrix `
  --profiles (Join-Path $dataDir "50个用户健康档案_详细版7.13.json") `
  --dialogues (Join-Path $dataDir "对话用例.json") `
  --recipes (Join-Path $dataDir "recipes_sample_2000.csv")
```

每次运行生成 `runtime/private_matrix_reports/<UTC时间>-<随机后缀>/`：

| 文件 | 内容 |
|---|---|
| `report.json` | 输入哈希、Git 提交、覆盖统计、每个档案 ID × 对话 ID 的状态、耗时与失败代码 |
| `report.md` | 便于人工审阅的矩阵汇总 |
| `failures.jsonl` | 一行一个档案 ID、对话 ID、失败代码 |
| `sessions.sqlite3` | 本次运行的会话状态，仅保留在被 Git 忽略的本地目录 |

报告只写数值 ID、状态、耗时、代码与哈希；不写健康详情、对话原文、模型密钥或完整响应。`runtime/` 已被 Git 忽略。尤其不要将 `sessions.sqlite3` 或原始文件添加到提交。

输出的 `coverage_complete` 只证明所有预期轮次执行完毕；`cells_with_failures` 反映本回放覆盖的工程断言。没有产生菜单的澄清场景无法完成菜谱与过敏核验，不能据此判定推荐正确。过敏文字词表无法核实品牌配方与交叉接触，缺少可靠份量时也无法计算个人定量营养。

运行器在覆盖不完整或存在失败代码时返回退出码 1，但仍会先写出报告，供后续 PR 分析。该报告属于内部诊断，不应写成评委评分。
