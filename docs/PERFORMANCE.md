# 流式性能与 TTFT 验收

## 测量口径

正式成绩由评测方从公网 API 实测。本项目的 Harness 使用同一客户端边界记录：

- `t0`：客户端开始发送 `POST /v1/chat/completions`；
- `time_to_headers_ms`：客户端收到 HTTP 响应头；
- `ttft_ms`：客户端收到第一个非空 `choices[0].delta.content`；空的 assistant role 块不计为首 Token；
- `e2e_ms`：客户端收到 `data: [DONE]`。

接口响应的 `Server-Timing` 仅用于定位解析、规划和解释阶段，不包含也不宣称服务端 TTFT。评分与优化以客户端实际观测值为准。

## 阈值

Harness 严格按照赛题中的“小于”关系判定，不把等于边界算入更高档：

| 指标 | excellent | qualified | exceeded |
| --- | ---: | ---: | ---: |
| TTFT | `< 2000 ms` | `< 5000 ms` | `>= 5000 ms` |
| 单轮完整响应 | `< 8000 ms` | `< 15000 ms` | `>= 15000 ms` |
| 多轮平均响应 | `< 6000 ms` | `< 12000 ms` | `>= 12000 ms` |

`excellent / qualified / exceeded` 是本地阈值标签，不是系统向评委申报的分数。报告同时保留失败请求、平均值、P50、P95、最小值和最大值，不能通过删除慢请求美化结果。

## 运行方式

先启动包含当前分支代码的 Compose 服务，再在 backend 容器内通过 frontend 代理测量完整链路。仅使用手写合成画像 `900001`，不发送原始健康档案。

```powershell
$env:DEMO_PORT="8081"
docker compose --project-name dietagent-pr5 --env-file configs/compose.env --file docker-compose.yml `
  up --build --detach
```

`8081` 用于避免与原 Demo 的 `8080` 冲突；容器内 Harness 仍通过 `http://frontend:8080` 访问本项目代理。

### 单轮串行基线

```powershell
docker compose --project-name dietagent-pr5 --env-file configs/compose.env --file docker-compose.yml `
  exec backend python -m evaluation.stream_performance `
  --base-url http://frontend:8080 --mode single --sessions 5 --concurrency 1 `
  --output runtime/performance_single.json
```

### 多轮串行基线

```powershell
docker compose --project-name dietagent-pr5 --env-file configs/compose.env --file docker-compose.yml `
  exec backend python -m evaluation.stream_performance `
  --base-url http://frontend:8080 --mode multi --sessions 3 --concurrency 1 `
  --output runtime/performance_multi.json
```

多轮场景依次发送“帮我安排一餐”“2个人，晚餐”“没有其他忌口，安排三道菜”，并复用每轮响应的 `X-Session-ID`。

### 并发能力

```powershell
docker compose --project-name dietagent-pr5 --env-file configs/compose.env --file docker-compose.yml `
  exec backend python -m evaluation.stream_performance `
  --base-url http://frontend:8080 --mode single --sessions 8 --concurrency 4 `
  --output runtime/performance_concurrent.json
```

并发测试使用相互独立的会话。报告中的 `success_rate` 必须结合延迟分布查看；出现 429、5xx、缺少正文或缺少 `[DONE]` 均计为失败。

## 报告与诊断

默认报告保存到容器的 `/app/runtime/performance_report.json`，该目录由 Docker volume 持久化。每个观测项包括 HTTP 状态、首正文时间、完整时间、字符数、会话 ID、错误代码和解析后的 `Server-Timing`。

优化时优先比较：

1. `time_to_headers_ms` 与 `agent_total`：差值较大通常表示排队、反向代理或网络开销；
2. `agent_parse`：需求解析模型调用；
3. `planning`：检索、规则、套餐组合与逐人校验；
4. `explanation`：验证事实解释阶段；
5. TTFT 与完整响应差值：当前 SSE 从已经验证的完整结果分块，该差值通常很小。

不得用空 role 块、虚假占位文本或自行上报的耗时替代真实首正文时间。

## 与功能回归合并运行

`python -m evaluation.regression_suite` 会先用 `/chat` 执行版本化合成场景及结构化断言，再把测试集中声明 `measure_performance` 的单轮/多轮场景通过 SSE 重放。一次运行的功能结果、TTFT、完整耗时、数据集哈希和内部诊断分保存在同一报告目录，适合比较优化前后变化。它不会取代上面的多会话并发基线；完整命令和文件说明见 [REGRESSION.md](REGRESSION.md)。
