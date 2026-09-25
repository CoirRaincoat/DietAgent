# 定性营养分析协议

本阶段借鉴 ZOE 对食材组成与适合原因的解释方式，不使用未验证的食物评分、精确营养量或健康效果。

`analyze_recipe(recipe, constraints)` 返回单菜结构，`analyze_menu(recipes, constraints)` 返回整餐结构；原有 `analyze()` 句子接口保留。Agent 的 `nutrition_analysis` 工具支持句子、单菜详情与整餐汇总。

| 字段 | 含义与边界 |
| --- | --- |
| protein_sources | 真实食材中识别出的蛋白质来源，不代表高蛋白 |
| carbohydrate_sources | 碳水来源，包括可识别的主食和添加糖来源，不估算摄入量 |
| fat_sources | 油脂及已识别食材来源，不把做法推断为低脂 |
| dietary_fiber | 蔬菜、豆类、全谷等可识别来源，不声称纤维达标 |
| ingredient_contributions | 每项食材的角色、解释、recipe_id、source_row 与是否记录用量 |
| goal_matches | preference_match / caution / insufficient_data，保留相反证据及规则来源链接 |
| suitable_reasons | 有来源的组成说明，不是个体适用性或疗效结论 |
| risks | 缺用量、复合配料、糖/钠来源、过敏检查范围、约束冲突等提示 |
| limitations | 对数据完整性、份量、烹调损耗与个体反应的明确边界 |

来源识别仅使用真实配料，不使用标题或原始健康宣传标签；未识别不能解释为“不含”。鸡腿菇、鱼露、蒸鱼豉油、牛肉高汤等不会被当作对应肉类蛋白来源。菜品类别和烹饪方式仍属启发式元数据。

健康偏好复用 `configs/rules.yaml` 中的 WHO / NIAMS 一般原则及其引用。缺少已配置规则的目标返回 insufficient_data，出现与目标需要关注的配料时返回 caution；已支持目标也不承诺低糖、低钠、低热量或治疗效果。定量分析需另行取得可核验的成分、数量、可食部、成品份数及分餐信息。

## 套餐搭配与营养分析的边界

套餐结构排序与 `nutrition_analysis` 的职责不同。前者是规划器内部的确定性信号，只使用已识别的 protein、vegetable、staple、soup 类别、烹饪方式以及明确冷热文字证据；后者说明真实配料能够支持的定性营养角色。内部排序分不进入 API，也不展示为系统自评。

对外的套餐搭配说明只列出可追溯的类别数量、做法和冷热证据，不据此推出营养素含量、荤素重量比例、个人摄入达标或健康效果。DeepSeek 只能选择程序提供的解释事实，不能读取或改写内部得分，也不能补造类别或宣称未记录的冷热属性。
