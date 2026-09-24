# 比赛 Demo 交付清单

本文件记录Phase 5—9历史交付。当前带用户界面的v0.3.0见[Phase 10报告](DEMO_V030_REPORT.md)。

题目：ZX-2026-0301 · 方太个性化膳食规划 Agent
版本：0.2.0 · 日期：2026-09-24 · 范围：Phase 5—9

## 完成内容

- Phase 5：全部 50 份真实健康档案自动生成 150 轮案例，加上原始 20 组 29 轮对话，完成本地验收和 PASS / FAIL / 数据不足报告。
- Phase 6：人数、餐次、忌口缺失时主动澄清；未知过敏原逐项澄清；通过四个注册工具执行检索、规则、菜单修改与营养分析。
- Phase 7：单菜及整餐的蛋白质、碳水、脂肪、纤维来源，逐食材贡献、健康目标匹配、适合原因与风险；不虚构营养数字。
- Phase 8：响应 schema_version=2.0 增加菜品卡片、原始食材明细、烹饪步骤、来源指纹及替换建议；合成 Demo 画像与原始档案外发阻断。
- Phase 9：实际构建 Docker 镜像并运行真实 HTTP 五轮 Demo，完善启动器、README、部署报告和演示流程。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 全量测试 | 246 passed，47.03 秒；1 条既有测试依赖弃用提示 |
| Ruff | app、pipelines、evaluation、tests 全部通过 |
| pip check | 无依赖冲突；当前 editable 包版本 0.2.0 |
| 真实数据本地验收 | 179 轮：156 成功、23 澄清；0 断言失败，668 次原始 CSV 来源核验 |
| 多轮状态 | 70 案例 PASS，0 FAIL |
| 过敏与来源 | 各 54 PASS、0 FAIL、16 数据不足 |
| 健康规则逻辑 | 50 PASS、0 FAIL、20 数据不足 |
| 定量健康效果 | 70 数据不足，未宣称营养量或医学效果达标 |
| 真实 DeepSeek + ASGI | 固定合成资料五轮通过 |
| Docker build/run + HTTP | PASS；五轮 HTTP 200，内置健康检查 healthy，完整验收 6.536 秒 |

真实验收使用本地人工 Intent fixture，验证工程规则与状态，不衡量真实模型的自然语言理解准确率。未产生菜单时过敏与来源检查标记数据不足。所有真实健康档案与原始对话均未发送给外部模型。

详细结果见 [真实数据报告](../evaluation/REAL_DATA_REPORT.md)、[部署报告](DEPLOYMENT_REPORT.md) 和 [开发日志](DEVELOPMENT_LOG.md)。本地 artifacts/ 保存机器报告且被 Git 忽略；原始数据保持不变。

## 分阶段 Git 记录

| 阶段 | 提交 | 内容 |
| --- | --- | --- |
| Phase 5 | 87e64d9 | 本地真实档案与对话验收 |
| Phase 6 | 4a16fee | 主动澄清与四个业务工具 |
| Phase 7 | 0f8e71a | 可追溯的定性营养解释 |
| Phase 8 | 6ffdda4 | 菜品呈现与仅合成资料实时联调 |
| Phase 9 | 388f179 | feat: validate Docker competition demo deployment |

执行 `git log --oneline -5 388f179` 可查看最终五个提交。所有提交保留在本地，未推送。

## 新增文件

以下相对路径以 Phase 4 文档提交 `02d01a3` 为基线，包含 Phase 5—9 的全部新增受版本管理文件。

- `app/agent/clarification.py`
- `app/api/presentation.py`
- `app/domain/cards.py`
- `app/infrastructure/synthetic.py`
- `app/nutrition/models.py`
- `app/nutrition/structured.py`
- `app/tools/__init__.py`
- `app/tools/health_check.py`
- `app/tools/menu_modify.py`
- `app/tools/nutrition_analysis.py`
- `app/tools/recipe_search.py`
- `app/tools/registry.py`
- `docs/COMPETITION_DEMO_DELIVERY.md`
- `docs/DEMO_FLOW.md`
- `docs/DEPLOYMENT_REPORT.md`
- `docs/NUTRITION.md`
- `evaluation/REAL_DATA_REPORT.md`
- `evaluation/demo_http.py`
- `evaluation/docker_run.py`
- `evaluation/real_data.py`
- `tests/test_cards.py`
- `tests/test_clarification.py`
- `tests/test_docker_run.py`
- `tests/test_nutrition.py`
- `tests/test_real_data.py`
- `tests/test_synthetic_data.py`

## 修改文件

- `.dockerignore`
- `Dockerfile`
- `README.md`
- `app/agent/service.py`
- `app/agent/tools.py`
- `app/api/main.py`
- `app/domain/models.py`
- `app/infrastructure/llm/base.py`
- `app/infrastructure/llm/deepseek.py`
- `app/nutrition/qualitative.py`
- `configs/intent_prompt.txt`
- `docs/API.md`
- `docs/ARCHITECTURE.md`
- `docs/DATA.md`
- `docs/DELIVERY.md`
- `docs/DEVELOPMENT_LOG.md`
- `evaluation/dialogues.json`
- `evaluation/offline.py`
- `evaluation/smoke_synthetic.py`
- `pyproject.toml`
- `tests/test_agent_api.py`
- `tests/test_llm.py`
- `tests/test_offline_replay.py`

## 演示入口与下一阶段

按照 [README](../README.md) 启动本地 API 或 Docker，再按 [五轮演示流程](DEMO_FLOW.md) 运行。Docker 验收容器已清理，dietagent:demo 镜像保留；当前没有保留临时 HTTP 服务。API 返回内容已可用于前端，尚未制作前端页面。

建议下一阶段依次完成：

1. 构建前端澄清对话、菜品与营养卡片、指定位置换菜及来源展示。
2. 使用独立合成语料评测真实模型意图解析、否定表达、过敏澄清和工具选择；区分规则正确率与 NLU 准确率。
3. 补齐权威食物成分、可计算用量与份量数据，再实施可校验的营养估算；维持缺失项未知。
4. 扩展多人限制、明确撤销/修改约束、并发与部署鉴权，然后推进多日计划。

当前仍为共享单餐、定性营养、本机单 worker Demo；没有宣称逐人营养达标、完整医学适用性或比赛评分结果。
