# 合成回归集与运行报告

## 目的与边界

`evaluation/cases/regression_v1.json` 是可公开提交、可版本化的合成测试集。它只使用手写合成画像 `900001`、`900002`、`900003`，不包含正式验收样例、真实健康档案或真实用户对话。

测试集当前包含 10 个独立场景，覆盖：

- 基础单餐推荐、档案过敏与菜谱真实性；
- 多人分别归因的过敏/忌口、宴请菜数与汤数；
- 主动澄清、约束追加、局部替换、整份否定、上下文解释和模糊需求。

功能检查读取 `/chat` 的结构化响应，验证 `recipe_id` 与来源、会话约束、逐人硬约束、营养结果与菜单 ID 对齐、换菜槽位和拒绝记忆。标记了 `measure_performance` 的场景会再次通过 `/v1/chat/completions` SSE 运行，用客户端边界测量 TTFT 与完整耗时。

## 每次测试生成什么

每次运行在 `runtime/regression_reports/<UTC时间>/` 创建独立目录：

| 文件 | 用途 |
| --- | --- |
| `report.json` | 数据集版本/哈希、Git 提交、逐场景逐断言、服务端阶段耗时、TTFT/E2E 和汇总 |
| `report.md` | 便于人工阅读和放入 PR 描述的摘要 |
| `responses.jsonl` | 合成场景的逐轮完整结构化响应，便于定位失败 |

`runtime/` 已加入 `.gitignore`，不会误把运行报告或未来的本地数据提交到仓库。若需要在 PR 中展示结果，复制 `report.md` 的摘要即可；不要提交正式测试集或真实用户响应。

报告记录数据集 SHA-256，只有使用相同哈希的运行才适合直接做版本前后对比。报告中的 `diagnostic_score` 明确标记为 `internal_diagnostic_not_official`，只是按 20/20/30/30 权重计算的内部回归信号，不是评委分数。

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

1. 新建下一版本文件，例如 `regression_v2.json`；
2. 更新 `dataset_version`；
3. 保留旧版本，便于历史报告复现；
4. 只使用合成资料，不把正式样例改写后公开；
5. 对运行器新增检查时同时补单元测试。
