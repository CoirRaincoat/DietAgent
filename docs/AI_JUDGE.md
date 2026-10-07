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
JUDGE_MAX_TOKENS=8192
```

裁判最好与生成回答的模型来自不同模型系列，以降低自我偏好。该接口要求服务兼容 `/chat/completions` 和 JSON mode。

`JUDGE_MAX_TOKENS` 控制每次评审的 completion 上限；未设置时保留兼容默认值 1800。思考模型的推理也可能消耗该预算。DeepSeek V4 Pro 的本地实测在 1800 上限下曾返回 `finish_reason=length`，正式 JSON 为空；可先使用 8192 并检查实际返回，不能把截断判断当作有效分数。增大上限可能增加费用和耗时，输出仍必须以 `stop` 结束且通过完整结构校验。记录模型、提示词哈希和预算，不混合不同配置的 A/B 判断。[DeepSeek 官方接口说明](https://api-docs.deepseek.com/api/create-chat-completion/)

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

## 人工校准闭环

### 1. 生成匿名 A/B 评审包

使用与 AI Judge 相同的基线和候选回归报告：

```powershell
.\scripts\run_judge_calibration.ps1 `
  -Mode Prepare `
  -BaselineReport "C:\path\to\baseline\report.json" `
  -CandidateReport "C:\path\to\candidate\report.json"
```

脚本生成：

- `human_review_packet.jsonl`：匿名 A/B 回答，交给评审员；
- `human_labels.csv`：人工填写 `A`、`B` 或 `tie`；
- `HUMAN_REVIEW.md`：评审说明，交给评审员；
- `human_blinding_key.json`：解盲映射，评审结束前不要交给评审员。

无法判断的场景保持 `winner` 为空，不应强迫人工选择胜者。评审员不能查看解盲文件，也不应知道哪个版本是候选版本。

### 2. 计算 AI 与人工一致性

人工完成 `human_labels.csv` 后执行：

```powershell
.\scripts\run_judge_calibration.ps1 `
  -Mode Evaluate `
  -JudgeReport "C:\path\to\ai-judge\report.json" `
  -ReviewDir "C:\path\to\human-review"
```

校准报告包含：

- 人工标注覆盖率；
- AI 可比较率与顺序不一致弃权数；
- 完全一致率；
- Cohen’s κ；
- 人工 × AI 混淆矩阵；
- 分歧和弃权场景明细。

默认诊断阈值为至少 5 个有效场景、完全一致率不低于 70%、κ 不低于 0.4。阈值会原样记录在报告中，可以通过 `-MinCases`、`-MinAgreement` 和 `-MinKappa` 调整。达到阈值不代表获得官方认可，只表示这批样本没有观察到明显的人机校准问题。

状态解释：

- `insufficient_human_labels`：人工有效样本不足；
- `excessive_judge_abstention`：AI 双顺序结果不稳定，可比较样本不足；
- `kappa_unavailable`：标签分布单一，κ 无法计算；
- `calibration_below_threshold`：一致性低于声明阈值；
- `calibration_thresholds_met`：本批样本达到声明阈值。

AI 双顺序结论不一致时按弃权处理，不使用均值或强制胜负掩盖不稳定性。
