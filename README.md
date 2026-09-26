# 方太个性化膳食规划 Agent

题目编号：**ZX-2026-0301** · **比赛 Demo v0.3.0**

在浏览器中描述用餐需求，Agent 主动澄清人数、餐次与忌口，再从真实菜谱库推荐一餐。支持菜品详情、定点换菜和有来源的定性营养解释。

> 这是可运行的公开快照，包含 2000 条菜谱和手写合成画像。真实健康档案、原始对话、密钥及包含私有数据的旧 Git 历史均未公开；见 [公开发布说明](docs/PUBLICATION.md)。

## 快速开始

需要 Python 3.11+ 和已启动的 Docker Desktop。以下为 PowerShell 命令：

```powershell
git clone https://github.com/CoirRaincoat/DietAgent.git
cd DietAgent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
```

在本地 `.env` 填入有效的 `DEEPSEEK_API_KEY`，然后首次构建并启动：

```powershell
.\.venv\Scripts\python.exe -m evaluation.demo_start --build
```

- **用户界面：[http://localhost:8080](http://localhost:8080)**
- **三分钟演示：[http://localhost:8080/demo](http://localhost:8080/demo)**
- [API 文档](http://localhost:8080/docs) · [健康检查](http://localhost:8080/health)

后续启动默认复用已有镜像，不自动构建或拉取；更新代码后请重新加 `--build`：

```powershell
.\.venv\Scripts\python.exe -m evaluation.demo_start
```

`--no-build` 仍兼容，`--port 8081` 可更换端口。构建需要访问 Docker 镜像仓库；出现 Registry 网络错误时检查 Docker Desktop 网络或当前终端代理配置，已有镜像可以用默认命令启动。

前端 Nginx 和内部 FastAPI 分别运行在容器中，`dietagent_demo_runtime` 命名卷保存会话。密钥由启动器解析后只传给后端，不进入浏览器、命令参数或镜像。不要直接使用 `docker --env-file .env`；启动器通过空 `configs/compose.env` 避免 dotenv 格式差异。

停止服务并保留会话卷：

```powershell
docker compose --env-file configs/compose.env stop
```

旧版 `dietagent-demo` 单容器若占用 8080，可先执行 `docker stop dietagent-demo`，不要删除数据卷。旧入口 `evaluation.docker_run` 仅提供 API。

## 如何演示

打开 `/demo`，选择以下案例之一；每次选择都会新建合成用户会话：

1. **减脂晚餐**：一句需求 → 点击澄清选项 → 菜单与营养来源。
2. **灵活换菜**：生成菜单 → “整餐不吃鱼，重新安排” → 查看更新后的条件和菜品。
3. **记住忌口**：生成菜单 → “不能吃辣” → 查看规则筛选后的结果。

每张菜品卡可打开完整配料、原始步骤和来源；“换一道”仅调整指定位置。营养页签展示蛋白质、碳水、脂肪、膳食纤维来源、目标匹配与风险。精确热量和摄入量显示“数据不足”，图片使用明确标注的示意占位。

同一餐明确否定整份菜单后，已否定菜品不会在后续推荐或候选建议中重新出现；普通“换一道”不形成永久禁忌。新增食材偏好会在硬约束允许时局部调整菜单，无法覆盖的偏好会给出提示；指定换菜仍保持指定范围。食材筛查及多轮状态修复说明见 [回归修复记录](docs/REGRESSION_FIXES.md)。

用户界面不接受体检档案上传；默认 3 个画像均为手写合成资料。真实 DeepSeek 联调也只使用合成需求。真实 50 份健康档案和原始对话仅用于宿主本地验收。

## 本地开发

已完成快速开始的环境无需重复安装。独立开发前后端时，可按以下命令准备 Python 环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
```

在 `.env` 配置有效 DeepSeek 密钥。两个终端分别运行：

```powershell
# 终端一：后端，项目根目录
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

```powershell
# 终端二：前端，Node.js 22.12+（本机已用24.14.1验证）
cd frontend
npm ci
npm run dev
```

开发界面为 `http://localhost:5173`，Vite 将 `/api/` 转发给本地 8000 后端。可通过 `API_PROXY_TARGET` 环境变量修改开发代理地址；不向前端传任何模型密钥。Docker 生产入口仍为 8080。

## 测试与验收

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app pipelines evaluation tests
npm --prefix frontend run build
```

公开版默认后端测试不需要私有健康档案或模型密钥。`evaluation.real_data`、`evaluation.offline` 等私有验收入口需要另行提供授权原始资料，不能从本快照直接复现。

浏览器测试在 frontend/ 执行，默认拦截 HTTP 返回独立手写合成响应，不调用模型：

```powershell
cd frontend
npx playwright install chromium
npm test
```

可通过 `E2E_CHROMIUM_EXECUTABLE` 复用已安装的 Chromium；`E2E_BASE_URL` 指向运行中的部署时不再启动 Vite。真实模型五轮浏览器测试须显式开启，只发送固定合成消息：

```powershell
$env:E2E_BASE_URL = 'http://localhost:8080'
$env:E2E_LIVE = '1'
npm run test:live
Remove-Item Env:E2E_LIVE
```

现有 HTTP/ASGI 验证入口仍可使用：

```powershell
.\.venv\Scripts\python.exe -m evaluation.demo_http --base-url http://localhost:8080
.\.venv\Scripts\python.exe -m evaluation.smoke_synthetic
.\.venv\Scripts\python.exe -m evaluation.stream_performance --base-url http://localhost:8080
.\.venv\Scripts\python.exe -m evaluation.regression_suite --base-url http://localhost:8080
```

`evaluation.regression_suite` 使用公开、版本化的 10 组合成场景，每次生成 JSON、Markdown 和逐轮 JSONL 报告；默认同时测量其中声明的 SSE 性能子集。报告为内部诊断，不是官方评分。完整 Docker 命令、报告字段和数据集升级规则见 [合成回归文档](docs/REGRESSION.md)。真实数据验收使用本地人工 Intent fixture，验证工程规则与多轮状态，不等于模型理解准确率。原始资料外部回放 `evaluation.replay` 不属于当前允许执行范围。

## 结构与能力边界

| 路径 | 用途 |
| --- | --- |
| frontend/src | Vue 对话、澄清、菜单、营养和演示页面 |
| frontend/tests | 合成 HTTP Mock 与显式开启的真实模型浏览器测试 |
| app/api、app/domain | API 与响应 schema_version=2.0 |
| app/agent、app/tools | 会话编排及四个业务工具 |
| app/retrieval、app/rules、app/nutrition | 检索、规则、可追溯定性营养 |
| app/infrastructure | 数据、合成画像、SQLite、DeepSeek |
| evaluation、tests、pipelines | 验收、后端回归与数据规范化 |
| docker-compose.yml | 前端 Nginx + 内部单 worker FastAPI |

当前是本机共享单餐 Demo，没有逐人份量、多日计划、鉴权、多 worker 协调或完整撤销约束。切换画像/新对话会清空页面会话和未发送草稿，刷新页面开始新会话；本阶段未提供历史会话恢复。候选建议只供查看，暂不支持指定候选 ID 强制替换。营养仅作定性解释，不判断个人摄入达标或医学效果。

真实健康档案与原始对话未改动、不公开上传；`.env`、`.venv/`、node_modules/、runtime/、artifacts/、构建及浏览器测试产物均被忽略。

查看 [v0.3.0 交付报告](docs/DEMO_V030_REPORT.md)、[前端架构](docs/FRONTEND_ARCHITECTURE.md)、[演示流程](docs/DEMO_FLOW.md)、[API](docs/API.md)、[合成回归](docs/REGRESSION.md)、[流式性能验收](docs/PERFORMANCE.md)、[真实数据报告](evaluation/REAL_DATA_REPORT.md) 和 [开发日志](docs/DEVELOPMENT_LOG.md)。后续 Phase 11 聚焦模型理解能力评测与比赛材料整理。

公开发布使用独立干净快照，排除私有数据与含私有数据的历史，见 [公开发布说明](docs/PUBLICATION.md)。
