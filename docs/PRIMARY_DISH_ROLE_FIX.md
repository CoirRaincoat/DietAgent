# 成菜主角色与食材来源分离

2026-10-01，本地实现提交 `fc99764`、米别名补丁 `6f15a52`。尚未推送，尚未完成整个算法长任务。

## 这次修什么

源菜谱归一化给每道菜一个互斥的主要成菜角色，而不是把每种配料都算成一道菜。鸡肉玉米肠不再同时计蛋白质菜、蔠菜和主食；蛋炒饭、猪肉饺子计主食；玉米排骨汤只占汤位；明确鸡蛋羹计蛋白质菜。饮料、甜点和加工记录仍不能填普通正餐菜位。

实现：`app/domain/dish_roles.py`、`app/infrastructure/data.py`。角色版本 `primary-culinary-role-v1`；菜单质量观察升为 `source-menu-diversity-v2`，不能与旧多标签 v1 数值直接比较。稳定菜谱 ID、原料、步骤、来源行、指纹仍按原始数据保留。

主要角色不等于营养来源：含肉末的鱼香茄子可以承担蔠菜角色，但营养来源仍保留肉末，不能叫纯素；汤和主食内的肉、蛋也仍可作为定性来源，但不同时占蛋白质主菜位。未估算蛋白质克数、配料比例或个人份量。

## 规则边界

顺序为非正餐证据、明确蛋羹、汤、具食材证据的主食形态、具食材证据的蛋白质菜名、蔠菜、根茎主食，再以源食材顺序作有限回退。跳过明确调味品；不把鸡精、鸡腿菇、牛肝菌当肉。未知仍为空。菜名与食材顺序都是启发式，不是完整烹饪知识库，也不代表纯素、低钠或适合某种疾病。

回放发现“莴笋菜饭”原料写作“米”；补精确别名，不能因所有词中含“米”就算主食。历史中间报告保留。当前 eligible 无角色记录为 94 条（上阶段 111 条），仍需逐条审核召回误拒绝，不能凭类别减少宣称准确率提升。

## 可复核证据

- 公开手写开发输入：`evaluation/cases/dish_roles_v1.json`，30 组，非官方数据、非留出集。
- 原 32 项测试在旧代码上 15 失败、17 通过；扩展 52 项后通过。回放再发现米别名遗漏，扩展到 55 项时 2 失败、53 通过；补丁后 55 项全部通过。
- 最终全量 815 项通过，1 条既有 TestClient 弃用警告，16.69 秒。这是本地测试耗时，不是模型响应时延。
- 新模块语句、分支覆盖率 100%；严格 mypy、Black、全仓 Ruff 检查通过。覆盖率不代表语义无误或全面质量合格。
- 人工复核包：本地 `runtime/primary_role_human_review/20260930T191002Z/`；含 Markdown/JSON、30 组输入 JSONL、55 项逐项结果 JSONL、3 份源菜单前后对照 JSONL、提交及输入哈希。
- JUnit：`runtime/primary-role-red.xml`、`runtime/primary-role-rice-red.xml`、`runtime/primary-role-rice.xml`、`runtime/primary-role-rice-full.xml`；覆盖率 `runtime/primary-role-rice-coverage.json`。

五人六菜含一汤的回放现在有独立的主食位，仍仅一道蛋白质菜；没有证明份量或多人荤素搭配理想。单人三菜选出皮冻也需要后续餐次适配审核。下一主题是人数/餐次相关的整餐目标和有限结构调整，保持指定局部换菜边界、已满足重试稳定与硬约束。

未调用真实生成模型或 AI Judge，未重测 TTFT/端到端性能，未代填人工标签。旧模型报告、评分与本地开发验证分别保存。

## 复跑专项

在当前仓库根目录，使用已安装锁定依赖的 Python：

```powershell
& ".\runtime\fix-test-env\Scripts\python.exe" -m pytest tests/test_primary_dish_roles.py -q -p no:cacheprovider --basetemp .\runtime\pytest-primary-review-new --junitxml .\runtime\primary-review-new.xml
```

选新的输出路径；不要覆盖历史失败或已有复核包。
