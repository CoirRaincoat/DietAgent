# 第一阶段 MVP 交付记录

本文件保留 MVP 阶段历史状态。当前比赛 Demo 的能力、验证及文件清单见 [比赛 Demo 交付记录](COMPETITION_DEMO_DELIVERY.md)。

项目：方太个性化膳食规划 Agent（ZX-2026-0301）。依据 `prompt/start.md` 完成工程初始化、数据层、Agent 核心、API 与测试四个阶段。

## 已交付行为

输入消息 → 加载用户画像 → DeepSeek 提取结构化需求 → 合并持久化约束 → 库内关键词检索 → 规则筛选 → 菜单规划 → 已验证事实解释 → FastAPI 返回。

支持共享单餐菜单、多轮约束记忆、指定换菜、解释当前菜单、澄清与无解响应。工具由程序编排；模型不能生成任意菜谱或营养数值。检索、规则、规划、营养说明、状态存储及模型适配分别位于独立模块。

## 新增或修改文件

下列为本次开发文件的完整清单。原始 `dataset/`、`docs/PROJECT_PLAN.md`、`docs/problem.docx`、`docs/references/` 及用户提供的 `prompt/start.md` 已纳入本地 Git，内容未因开发而修改。`.venv/` 是基于当前 Python 3.13.15 创建的本地环境，不纳入 Git。

```text
.dockerignore
.env.example
.gitattributes
.gitignore
app/__init__.py
app/agent/__init__.py
app/agent/planner.py
app/agent/service.py
app/agent/tools.py
app/api/__init__.py
app/api/main.py
app/domain/__init__.py
app/domain/models.py
app/infrastructure/__init__.py
app/infrastructure/data.py
app/infrastructure/llm/__init__.py
app/infrastructure/llm/base.py
app/infrastructure/llm/deepseek.py
app/infrastructure/sessions.py
app/infrastructure/settings.py
app/nutrition/__init__.py
app/nutrition/qualitative.py
app/retrieval/__init__.py
app/retrieval/keyword.py
app/rules/__init__.py
app/rules/engine.py
configs/intent_prompt.txt
configs/rules.yaml
Dockerfile
docs/API.md
docs/ARCHITECTURE.md
docs/DATA.md
docs/DELIVERY.md
docs/DEVELOPMENT_LOG.md
evaluation/__init__.py
evaluation/dialogues.json
evaluation/offline.py
evaluation/replay.py
evaluation/smoke_synthetic.py
pipelines/__init__.py
pipelines/normalize.py
pyproject.toml
README.md
requirements.lock
tests/test_agent_api.py
tests/test_data.py
tests/test_llm.py
tests/test_offline_replay.py
tests/test_planner.py
tests/test_rules.py
tests/test_sessions.py
```

## 运行与 Demo

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --workers 1
```

运行后访问 `http://127.0.0.1:8000/docs` 或 `/health`。依赖重建和 `.env` 配置见 [README](../README.md)。API 输入、输出和连续对话示例见 [API 文档](API.md)。

不读取或外传原始健康档案的三轮真实模型 Demo：

```powershell
.\.venv\Scripts\python.exe -m evaluation.smoke_synthetic
```

该命令使用自造画像 900001，依次推荐、替换第二道菜、解释当前菜单；通过 ASGI 接口运行，不需要另外启动服务，需要有效 DeepSeek 密钥并会产生 API 请求。

## 验证结果与边界

| 验证 | 结果 |
| --- | --- |
| Python 回归测试 | 161 项通过；1 条测试依赖弃用提示 |
| Ruff 与依赖一致性 | 均通过 |
| 原始场景本地回放 | 20 组、29 轮；23 次菜单、5 次澄清、1 次无解；断言无失败 |
| 菜谱来源一致性 | 本地回放 72 项检查通过 |
| 合成资料真实 DeepSeek | 三轮通过，约 0.69—1.02 秒/轮，仅是本次样例观测 |
| 原始数据完整性 | 三个原始数据文件 SHA-256 未改变 |
| 本机 HTTP 健康检查 | 正常加载 2000 条菜谱和 50 份档案 |
| Docker | 配置已提供；本机引擎未启动，构建及运行未验证 |
| 原始健康档案外部回放 | 此前自动审批拒绝，未执行；用户现已确认仅用合成资料联调，原始资料仅用于本地验证 |

本地回放采用人工 Intent 标注，只验证下游业务，不评价真实模型自然语言理解；没有把当前结果视为竞赛准确率。精确营养、可核验总耗时、份量计算、逐人营养、完整约束撤销、SSE 和多 worker 仍未实现。有限过敏词典不能保证穷尽品牌配料或交叉接触。

## 下一阶段优先顺序

1. 审核食材别名、复合配料、菜品角色与适用规则；增加独立人工预期评测集；原始场景继续本地评测，真实模型评测扩充合成场景。
2. 接入有来源的食材营养、可食部、用量、份数及烹饪耗时数据，再开发可追溯定量分析。
3. 完善约束撤销、逐人成员档案及份量分配；补充含冲突和模糊输入的多轮评测。
4. 完成 Docker 实测，再按部署需要加入鉴权、共享会话协调、并发压力测试和流式反馈。

Git 提交按工程、数据、模型适配、规则规划、Agent 状态及 API 验证分阶段组织；使用 `git log --oneline --reverse` 查看本地提交记录。未推送远程仓库。
