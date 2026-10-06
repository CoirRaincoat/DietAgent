# eval: 核健康参考作用范围与联动排菜，不将代理加分当质量验收

## 变更

- `7877916`新增显式 `--health-category-scope`，在原整餐solver里组合来源类别作用域、主体和方法策略；默认仍关闭，不改变app/configs、原2000、服务依赖或硬约束。
- 原步骤设备程序缺失、未具名蘸酱只作人工复核线索；原收尾“蒸好”不当具体程序，否定／说明反例保留。不能凭来源线索猜配方、营养、份量或疗效。
- 生成逐例原菜单／新菜单、来源／合同／参考向量、实际耗时、空真人意见与公开双序AI加页。

## 实际结果与拒绝接纳

20复用开发条件（18原2000／2公开）120菜单调用＋5无优化诊断，18参考改善调用仅来自3条件／6菜单，含重复回放。38固定基线可行、30无健康不扰动、30无改善、4不支持（同一条件4模式）。后验合同无违反、精确回放一致，不代表用户质量或全库准确率。

新菜单仍有原肘子1100g、90分钟炖煮后冷藏2小时；配方份数未知不能当一人份。多人蛋50g／虾皮5g羹、设备屏幕程序未展开、未具名蘸酱等需复核。家庭还留披萨／肘子，纯素未改善，故不启用。跨策略参考向量不可当质量增长；真实构建／本地求解0.000145–4.532079s不是TTFT。

新授权公开5自编案例×双序DeepSeek `deepseek-flash`：10有效／0失败／0重试，4平／1旧优／0新优、双序一致。3组完全相同控制不可计质量胜出，1清洁换菜平，1设备程序缺失旧优。未人工校准、个别裁判文本外表述保留，不充当官方、临床或独立60留出分数。输入11501／输出28511／总40012 tokens；未核账单，不报人民币费用。此额度已用尽，不复用到下一主题。

## 验证与证据

- 新37合同；隔离152通过／1配置警告；主4377通过／4隔离模块跳过、1旧警告，99.48s。红测1失败36通过保留。4文件Ruff/Black、2模块strict通过，非全项目认证。
- 人工入口：`runtime/health_scope_combination_review/20261004-sealed/summary.html`及summary.md；完整report.json/md/html、records.jsonl、comparison.json、20空human_labels.jsonl、公开AI加页。
- 72材料SHA、源字节／完整正规化相等、前后源码归档。旧manifest中的变动live源码用原f8f81b7归档核验，不冒称历史live一直匹配。静态HTML未浏览器视觉验收。
- 公开AI只四张自编卡片；原2000／官方／私有发送0。源回放没有新生成HTTP／NLU／真人／TTFT。第三方裁判调用与生成时延分开。

复现：使用隔离solver环境运行 `python -X utf8 -m evaluation.run_whole_menu_solver --source dataset/recipe_kb/recipes_sample_2000.csv --source-snapshot runtime/light-full-preparation-after-final.json --api-snapshot runtime/light-full-preparation-api-after-final.json --conditions runtime/health-reference-pairs-20261004.json --output <新的未占用目录> --seconds 30 --food-identity-ablation --health-category-scope`。重跑不是新独立样本，也不得覆盖封存材料或重新运行已耗尽AI授权。

## 边界与下一步

WHO与DASH强调整膳食结构、份量与钠情境，未提供本项目单菜工程权重；不作医疗功效推荐。[WHO Healthy diet](https://www.who.int/news-room/fact-sheets/detail/healthy-diet)；[NHLBI DASH](https://www.nhlbi.nih.gov/health/dash-eating-plan)。

下一先修原步骤未知蘸酱在不辣／过敏仍放行的有限缺口，再验真实菜单效用。137唯一词汇缺项是覆盖审计，不是137坏菜，不用粗暴白名单。卡住时查GitHub、核许可与同集净收益；不自动推送／合并。独立留出、真人校准、官方私有ID、营养份量、协作者差异与最终总结仍待推进。
