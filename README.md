# 方太个性化膳食规划 Agent

## 10月6日最终候选入口

最新业务基线为`7d314947985b0ae70c777eccc9d450f2ee3295a0`；准确含文档源码提交、ZIP及SHA只认本地`runtime/delivery_final_20261006-7d31494/human-review/verification.json`的success=true与实际包哈希。不用旧4b16f35镜像认证新源码，不推带私有祖先的整合历史。最新步骤与未完成项见[冻结清单](docs/DELIVERY_FREEZE_20261006.md)、[本轮交付快照](docs/PR_FINAL_SOURCE_FREEZE.md)；源码包、部署文档、[技术方案](docs/TECHNICAL_SOLUTION.md)和[API](docs/API.md)为交付范围。

默认路径已含鱼主体做法、便当准备、明确食物需求保存和近期同角色轮换纠错；N06四餐蛋白菜已有实际改善，M03不辣凉粉仍未满足，实际口味／厨房／份量和人审不由工程检查代替。原2000不改，原档案1–50单独本地只读挂载，Key仅服务端配置、不随包发布。10月7日验证打包，用户8号前提交；不自动推送、合并、提交平台，不新增未授权模型费用。下面记录保留为历史，以本段和冻结清单顶部为当前状态。

10月5日本地整合候选：融合已合并的PR #16与菜单修复，保留需求匹配、原方定性健康、跨餐历史、有限本地蒸/煎豆腐补位和局部/整餐做法更新。最近5组同输入完整菜单保持，本次整合没有新增菜单质量收益；不放蒜硬约束遗漏等仍待修。公开提交须不携带旧私有历史，未自动推送；详见[整合与验收边界](docs/BRANCH_INTEGRATION_REVIEW_20261005.md)。

10月5日新增首版有限生成兜底：原库无合规豆腐主体且用户明确想要豆腐时，可在默认推荐中补入本地清蒸豆腐方案，仍核硬限制/数量/局部权限。新菜明确标“新生成、待试做”、CSV行为null，不回写2000库、不编营养功效或份数；首版不是任意菜式LLM创作。H02实际前后见`runtime/generated_tofu_20261005/human-review/report.md`，人工待确认；来源契约与限制见[API文档](docs/API.md)。已有M01/M05完整源菜单保持，0新收费/外发，未自动推送、合并或提交比赛。新代码还需冻结候选镜像/平台验收。

题目编号：**ZX-2026-0301** · **比赛 Demo v0.3.0**

在浏览器中描述用餐需求，Agent 主动澄清人数、餐次与忌口，再从真实菜谱库推荐一餐。支持菜品详情、定点换菜和有来源的定性营养解释。

> 这是可运行的公开快照，包含 2000 条菜谱和手写合成画像。真实健康档案、原始对话、密钥及包含私有数据的旧 Git 历史均未公开；见 [公开发布说明](docs/PUBLICATION.md)。

## 快速开始

10月5日本地比赛候选的默认菜单与健康排序已更新，尚未自动推送/合并，直接克隆远端不保证包含这些修改。交付必须锁定源码包的完整commit，而非复用旧`dietagent:demo`镜像。现行验证范围和无付费的隔离Docker验证见[交付预检](docs/DELIVERY_PREFLIGHT_20261005.md)；旧部署PASS不代表本候选通过。

需要 Python 3.11+ 和已启动的 Docker Desktop。以下为 PowerShell 命令：

先确认当前终端的`python --version`。若它指向旧Anaconda（例如3.9），请先切换为已安装的Python 3.11+；下面的检查会在创建环境前停止不兼容解释器。检查通过只代表宿主版本达标，不等于Docker目标Python3.13、依赖或比赛平台已验收。

```powershell
git clone https://github.com/CoirRaincoat/DietAgent.git
cd DietAgent
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw '当前python低于3.11或不可用；请先选用Python 3.11+解释器，不要继续创建环境。' }
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

比赛官方1–50 ID支持**显式本地档案加载**，不配置仍三合成。原档案不进镜像/公开包；模型仅解析用户文本＋白名单结构，档案/菜谱/排菜/说明本地，不发助手历史，original不再直接403。本地MockTransport验证不是实际模型/平台验收；见[本地ID接入](docs/LOCAL_OFFICIAL_IDS.md)与[默认模型边界](docs/USER_ONLY_MODEL_BOUNDARY.md)。

默认API支持明确“安排下一餐”后沿用需求、参考同ID最近8份已推荐菜单；普通继续/解释/重试与局部换菜不轮换整餐。不是多日摄入计划或实际食用记录，也不保证全部不重复。当前固定M02四餐已消除此前第4餐的两道相邻重复，原方发酵时间、软菜比例和实用性仍待人工；其他solver/扩池/内容轮换实验仍OFF。见[交付计划](docs/DELIVERY_PLAN_20261007.md)，不能把固定案例去重写成所有推荐均改善。

默认护心菜单已支持有原方依据的蛋白主体选择；M05同输入肘子→鱼片，其余5道不变，非低钠/疗效/五人份认证。普通含钠来源与一般方法5→4的取舍明示，硬安全、点名要求和局部权限保留；见[实现、实际收益与残余](docs/PR_M05_HEART_PROTEIN_BODY.md)。

打开 `/demo`，选择以下案例之一；每次选择都会新建合成用户会话：

1. **减脂晚餐**：一句需求 → 点击澄清选项 → 菜单与营养来源。
2. **灵活换菜**：生成菜单 → “整餐不吃鱼，重新安排” → 查看更新后的条件和菜品。
3. **记住忌口**：生成菜单 → “不能吃辣” → 查看规则筛选后的结果。

每张菜品卡可打开完整配料、原始步骤和来源；“换一道”仅调整指定位置。营养页签展示蛋白质、碳水、脂肪、膳食纤维来源、目标匹配与风险。精确热量和摄入量显示“数据不足”，图片使用明确标注的示意占位。

同一餐明确否定整份菜单后，已否定菜品不会在后续推荐或候选建议中重新出现；普通“换一道”不形成永久禁忌。新增食材偏好会在硬约束允许时局部调整菜单，无法覆盖的偏好会给出提示；指定换菜仍保持指定范围。食材筛查及多轮状态修复说明见 [回归修复记录](docs/REGRESSION_FIXES.md)。

2026-10-05 融合版增加普通忌口的确认撤销、最初菜数恢复和最初菜单精确恢复。恢复前重新核对当前硬约束，后来新增的过敏或忌口继续保留；恢复失败不撤销拒绝记录。多人档案身份未关联时继续保守核对共享限制。范围与离线证据见 [三方融合说明](docs/THREE_SOURCE_INTEGRATION_20261005.md)。

附件的数据治理工作已收敛为可选的来源元数据缓存和统一检索接口，默认仍为词法召回；不会自动连接向量服务或下载模型。见 [附件融合范围与用法](docs/ARCHIVE_INTEGRATION.md)。

用户界面不接受体检档案上传；默认 3 个画像均为手写合成资料。真实 DeepSeek 联调也只使用合成需求。真实 50 份健康档案和原始对话仅用于宿主本地验收。

## 本地开发

已完成快速开始的环境无需重复安装。独立开发前后端时，可按以下命令准备 Python 环境：

```powershell
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw '当前python低于3.11或不可用；请先选用Python 3.11+解释器，不要继续创建环境。' }
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

完整的 Docker 自测、真实模型回归、性能测量、可选私有矩阵和严格证据卡可以通过一条命令运行：

```powershell
.\scripts\run_self_assessment.ps1
```

输出写入 `runtime/self_assessments/`；回归门禁、私有数据参数和报告解释见[一体化自测文档](docs/SELF_ASSESSMENT.md)。已知合成用例全过不会生成推荐质量 100 分。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app pipelines evaluation tests
npm --prefix frontend run build
```

公开版默认后端测试不需要私有健康档案或模型密钥。`evaluation.real_data`、`evaluation.offline`、`evaluation.private_matrix` 等私有验收入口需要另行提供授权原始资料，不能从本快照直接复现。

PR #2–#5 的问题复现、修复范围和验证证据见 [2026-09-27 审查与维修报告](docs/reviews/DietAgent_PR_Review_20260927.md)。

当前创新点与证据边界见 [创新点草稿](docs/INNOVATION_DRAFT.md)；后续算法优化的优先级、前置数据条件和逐项验收门槛见 [算法改进 TODO](docs/ALGORITHM_TODO.md)。

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

`evaluation.regression_suite` 使用公开、版本化的 30 组合成场景，每次生成 JSON、Markdown 和逐轮 JSONL 报告；默认同时测量其中声明的 10 次 SSE 性能请求。报告为内部诊断，不是官方评分。完整 Docker 命令、报告字段和数据集升级规则见 [合成回归文档](docs/REGRESSION.md)。[私有矩阵回放](docs/PRIVATE_MATRIX.md) 对本地 50 份档案与 20 组未绑定用户的对话建立独立会话，生成脱敏报告。真实数据验收使用本地人工 Intent fixture，验证工程规则与多轮状态，不等于模型理解准确率。原始资料外部回放 `evaluation.replay` 不属于当前允许执行范围。

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

查看 [v0.3.0 交付报告](docs/DEMO_V030_REPORT.md)、[前端架构](docs/FRONTEND_ARCHITECTURE.md)、[演示流程](docs/DEMO_FLOW.md)、[API](docs/API.md)、[合成回归](docs/REGRESSION.md)、[流式性能验收](docs/PERFORMANCE.md)、[独立 AI 双盲评审](docs/AI_JUDGE.md)、[真实数据报告](evaluation/REAL_DATA_REPORT.md) 和 [开发日志](docs/DEVELOPMENT_LOG.md)。后续 Phase 11 聚焦模型理解能力评测与比赛材料整理。

公开发布使用独立干净快照，排除私有数据与含私有数据的历史，见 [公开发布说明](docs/PUBLICATION.md)。
