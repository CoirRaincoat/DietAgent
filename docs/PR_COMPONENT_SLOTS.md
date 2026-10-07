# 修复调味组件与未拆分套餐误占正餐菜位

## 目的

禁止锅底、肉酱、生绞肉、蛋白糖霜及多菜记录仅因含肉、蛋清或“烹饪结束”而成为一道正餐；不通过补餐次标签掩盖这些问题。

实现提交：`9e1b2a7fe3ad106d6d2690c9f7d84aea5dedb77a`。对照基线：`209d9626056c1d7bad1aa42d87bce09e50ade589`。仅本地准备，未推送或合并。

## 改动

- 新增有限原文组件证据：独立锅底/汤底/肉酱/烧烤酱/糖霜、无加热的裸肉加工、多菜数量或套餐独立摆放。
- 营销“套餐”不直接排除；已组装汉堡、牛排配菜同盘、酱料配主菜和实际熟成肉末保留。
- 消费于现有归一化正餐角色和原文门禁；菜单、建议、旧缓存都不能靠错误protein标签恢复组件。
- 局部换菜之外有无效菜位时，请用户确认整餐调整；无有效候选时失败并说明，不凑菜数。

## 验证与报告

- 新增54项源/公开边界、硬限制、局部范围、HTTP/SSE和重启专项；全量2565通过，1条既有TestClient弃用警告（45.55秒）。
- Ruff五文件、严格mypy两模块、Black四文件通过；不代表全项目静态检查。
- 原2000条逐项对照：原料、步骤、标签、ID及指纹一致；仅11条派生categories/eligible/quality_flags变化，原餐次参考绑定仍通过。
- 44组固定同输入、21组菜单变化、历史10组均不变；34组有限行为12→33通过，**不是质量分或全需求验收**。
- 真实失败仍保留：公开单菜标题“1菜0汤”被旧角色规则误当汤，后续修复。
- 初始33失败/10通过、边界2失败/49通过及首版报告32/34保留。首版失败后“继续保持已接受菜单”检查不适用，修正口径后新报告33/34，不覆盖原件。

正式本地报告：`runtime/component_slot_review/candidate-20261002T061222Z-104b9c2f/report.html`，同目录提供`report.md`、`report.json`、`comparisons.jsonl`、`inputs.json`、完整2000源记录前后、11条变化与44行空白人工意见。首版报告`runtime/component_slot_review/candidate-20261002T060738Z-bce52522/report.html`独立保留。两版各49份材料SHA和8链接核验一致。

## 边界

- 不自动拆套餐、扩写做法、补健康功效或营养克数；原菜谱份量与人群适配未认证。
- 新规则是有限源反例修复，不是全库正餐资格或食用安全审核。
- 本轮真实生成/外部AI调用0；先前10次DeepSeek授权已用完，不自动追加费用。未由助手代填人工评分，独立复核待做。
- 三次离线planner耗时不是模型首token；未宣称性能提升。未做浏览器视觉QA。
- “0汤”标题误判、隐式做法过度保护、餐次/偏好冲突、一般负向偏好、同质化、检索/组合、独立留出与人工校准仍待推进。

## 本地重跑

从该工作树根目录运行，使用已准备的Python 3.12环境；每次选择新的临时目录，避免覆盖历史结果。

```powershell
Set-Location "C:\Users\jack\Documents\Codex\2026-09-22\ui-ui-2\.slim\worktrees\ai-judge"
New-Item -ItemType Directory -Force .\runtime | Out-Null
.\runtime\fix-test-env\Scripts\python.exe -X utf8 -m pytest tests/test_component_slots.py tests/test_component_slot_integration.py -q -p no:cacheprovider --basetemp runtime/pytest-component-review-new
```
