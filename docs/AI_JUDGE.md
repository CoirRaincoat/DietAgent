# 独立 AI 双盲评审

`evaluation.ai_judge` 使用独立的 OpenAI-compatible 模型比较两次合成回归运行。它补充搭配、交互和解释质量观察，不替代确定性的过敏、忌口、菜谱真实性与性能门禁。

## 评审原则

- 只接受 `data_scope=synthetic` 的回归报告，拒绝发送私有用户矩阵。
- 两份报告必须使用相同的数据集 SHA-256、场景和用户消息，且确定性回归全部通过。
- 每个场景执行两次：先比较 A=基线/B=候选，再交换为 A=候选/B=基线。
- 两次映射后的胜者不一致时记为 `inconclusive`，不强行选胜者。
- 不生成百分制，也不把模型评审分称为官方成绩。
- 裁判只接收压缩后的菜单事实，不接收冗长步骤、原始私有档案或 API 密钥。

## 裁判维度

每个维度采用 1–5 分，仅用于同一测试集上的版本比较：

- `requirement_fulfillment`：是否完整响应本轮和上下文需求；
- `menu_coherence`：套餐角色、原料重复、做法与整体搭配；
- `interaction_quality`：澄清、否定、追加条件和上下文处理；
- `minimal_change`：多轮修改是否只改变必要部分；
- `explanation_quality`：理由是否具体、简洁、可核验且不过度宣称。

## 配置

在单独的本地配置文件中填写裁判模型，不要提交该文件：

```dotenv
JUDGE_API_KEY=replace-me
JUDGE_BASE_URL=https://provider.example/v1
JUDGE_MODEL=independent-judge-model
JUDGE_TIMEOUT_SECONDS=90
```

裁判最好与生成回答的模型来自不同模型系列，以降低自我偏好。该接口要求服务兼容 `/chat/completions` 和 JSON mode。

## 运行

```powershell
.\scripts\run_ai_judge.ps1 `
  -BaselineReport "C:\path\to\baseline\report.json" `
  -CandidateReport "C:\path\to\candidate\report.json" `
  -SettingsFile "C:\path\to\judge.env"
```

脚本通过 Docker 运行，输出到 `runtime/ai_judge/<run-id>/`：

- `report.json`：完整结构化结果；
- `report.md`：便于审阅的摘要；
- `judgments.jsonl`：逐场景双顺序证据。

## 结果解释

重点查看候选胜、基线胜、平局、顺序不一致的数量和逐场景证据。顺序一致率低说明裁判不稳定，应更换裁判模型、调整量表或进行人工复核，而不是采用均值掩盖分歧。

首次启用时，应抽取一部分场景进行人工盲评，并比较人工与裁判的一致率。未经过人工校准的裁判结果只能作为研发诊断证据。
