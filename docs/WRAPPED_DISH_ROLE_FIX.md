# 包馅成菜主角色：修复与复核

2026-10-01。源回放基线 `607def480b0b4f057205869bed51a3507061a9f2`，候选 `415b6a15ed728e79baad3aebe19ccf1d0290847a`。功能提交175ed6b、独立验收415b6a1；仅本地提交，未推送或合并。

## 修复范围

荠菜石榴包是豆腐衣包荠菜肉馅蒸制，不应因菜名带荠菜而占蔬菜整菜位。主角色v5有限核对外皮原料与同句包入/裹入/卷起/卷成证据：蔬菜叶需另有蛋白质配料，豆制品外皮本身支持蛋白质成菜角色。肉末配菜、鸡粉、鸡腿菇、单独配卷、汤和粮食面皮等正向对照保护。不证明肉菜数量、营养克数、份量、熟化或临床适配。源配方未改，荠菜包仍在目录，只是不再作蔬菜位建议。

独立 `evaluation/role_oracle.py` 从另行审核的源ID→角色表核对被测目录，不导入生产分类器或读取返回成功标志。验证器v17可选 `expect.primary_roles` 同验菜单及建议，缺审核/观察或不符则失败并阻止质量观察。源真实性、安全和资格需另验；公开v3原字节未变。

## 测试与对照

Windows Python3.12.14、锁定依赖：156专项/1591全量通过，1条既有TestClient警告；严格类型检查、窄Black、Ruff通过。两模块116语句/64分支覆盖100%，不是全仓库或条件组合穷尽。3个公开函数有docstring、无新端点，窄存在性审计不证明全部文档完备。

旧代码7项包馅失败。公共入口首次2项是作者夹具/调用错误，修正后复现2项缺门禁失败，红灯分别保留。全部XML、测试定义和覆盖结果随复核包保存。

10份同输入结构化源请求主要角色（含建议）9/10→10/10；本次审核正餐范围、源身份、总数/汤数、有限不辣、局部范围与普通继续稳定各10/10；请求健康关注项披露仍9/9，另一份未请求。2000条触发扫描仅荠菜包角色变化，eligible未知87条未变，非全库审核/召回率。源CSV SHA：`b2177dc6cdcae24fc5671c8dada44295228f4301e3ce620ed11d51b1abfe4371`。

前后主菜单全部未变，6道含1汤的蔬菜位建议去掉误分类荠菜包。多目标与7道含2汤仍有南瓜重复/蒸煮集中，角色绿灯不代表整体质量提升。

## 复核与复测

本地 `runtime/wrapped_role_review/20261001T015012Z/`：report.html并排折叠；report.md/JSON/JSONL含完整前后菜单、建议、原料步骤、逐项结论。examples.jsonl为156项已知开发参数及10份源请求，共166项，非独立留出/官方20组。review_notes.csv留空，版本可见，非盲评校准。26个manifest文件SHA已核验。

无新生成模型/AI Judge调用，人工标签未填；TTFT、营养克数、份量及临床效果未测。源回放只读菜谱并用合成档案，不加载原始用户/官方对话。离线规划计时不能证明模型速度达标。

在本仓库、已有锁定环境内执行；pytest临时目录不存数据或报告：

```powershell
$env:PYTHONIOENCODING='utf-8'
& ./runtime/fix-test-env/Scripts/python.exe -m pytest tests/test_wrapped_dish_roles.py tests/test_role_oracle.py tests/test_primary_dish_roles.py tests/test_source_health_replay_roles.py -q -p no:cacheprovider --basetemp ./runtime/pytest-wrap-doc-check --junitxml ./runtime/wrap-doc-check.xml
```

下一项先量化同餐主食材/成菜做法重复及同等适配替换；跨餐历史、检索/候选求解实验、留出与真实裁判仍在TODO。
