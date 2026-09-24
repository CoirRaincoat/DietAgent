# 数据导入与可追溯性

> 公开版仅附带菜谱 CSV 与代码内手写合成画像。下文真实健康档案、原始对话、人工标注和其专用测试仅存在于私有本地环境；公开克隆的界面、默认测试和 Docker 启动不依赖它们。详见 [公开发布说明](PUBLICATION.md)。

数据层以只读方式加载 `dataset/` 中三个赛题文件，保留全部原始记录。文件 SHA-256、记录数量和质量统计写入可重建的 `artifacts/data_quality.json`。真实健康档案仅用于本地离线验收，运行产物不纳入 Git。HTTP Demo 使用单独的合成画像加载器。

| 数据 | 编码 | 结构 |
| --- | --- | --- |
| `dataset/recipe_kb/recipes_sample_2000.csv` | GB18030，严格解码 | 2000 行；名称、食材清单、烹饪步骤、label |
| `dataset/user_profile/50个用户健康档案_详细版7.13.json` | UTF-8 | 50 份用户档案 |
| `dataset/evaluation/dialogues.json` | UTF-8 | 20 个场景、29 条用户消息；无档案绑定、答案或评分标签 |

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pipelines.normalize
```

产物为 `recipes.normalized.json`、`profiles.normalized.json`、`recipes.lexical_index.json` 和 `data_quality.json`。这些文件属于派生数据；本地验收可直接加载原始数据，不依赖预先存在的产物；HTTP Demo 只加载原始菜谱 CSV 和手写合成画像。`--output` 可指定输出目录，但不允许写入原始 `dataset/`。

## 菜谱规范化

菜谱 ID 取原始四字段规范 JSON 的 SHA-256 前 24 位，加 `recipe_` 前缀；完整摘要与 CSV 行号同时保存。改变 CSV 行序不改变 ID，同名但食材或步骤不同的版本具有不同 ID。完全相同的重复行也保留，以出现次数后缀区别；当前数据没有这种重复。同名记录有 75 组，不按菜名去重。

`raw_ingredients`、`steps`、`raw_label` 保存原始字段；每项食材保留自己的 `raw`。食材只移除分组前缀、处理备注，识别明确的数字、分数和单位（例如 1/2 小勺转为 0.5 小勺）。`g`、`kg`、`mL` 等仅统一单位名称，不进行重量换算；来源的 t/T 单位保留写法，不猜测勺容量。适量、少许、半根、范围及混杂用量不转换为确定数值，`quantity`、`unit` 保留为空。食材别名和过敏映射由规则配置维护，不在数据层推断替换。

标签采用有限允许清单，仅提升餐次、口味、菜系和菜点类型。原始健康或人群标签不代表已验证结论，不作为健康规则依据；长段说明、JSON 残片及未知标签保留在 `raw_label` 并标记 `discarded_label_tokens`。

`categories` 包括 `protein`、`vegetable`、`staple`、`soup`、`dessert`、`drink`、`component`，烹饪角色根据菜名与主要原料进行启发式分类；其中 `protein` 必须在实际食材中发现蛋白来源，不从菜名推断，鸡精、鸡粉、蚝油等调味料及蟹味菇等名称相似的植物不作为证据。检查完整原料表，避免油盐水列在前面造成遗漏。这是检索信息，不能据此宣称“高蛋白”“低糖”。`methods` 是名称和步骤中的烹饪方式关键词，不能推导总耗时；`meal_types` 只来自已识别餐次标签，缺失时为空。

记录质量标志包括 `missing_steps`、`missing_name`、`missing_ingredients`、`unparsed_ingredients`、`missing_labels`、`discarded_label_tokens`、`ambiguous_ingredient_quantity`、`processing_component`。前四项与加工中间产品令 `eligible=False`；缺失标签或用量不明确不会单独禁止推荐。缺步骤的“奶油打发”、面团发酵等中间产品、“自定义”烹饪程序及测试占位菜均保留，但不作为可推荐成菜。甜品、饮品与正餐是否匹配，由规划模块进一步决定。

## 用户档案规范化

统一字段名并保留完整 `raw`，身高、体重明确采用厘米和千克。孕周期如 `22周` 转为整数 22，空值保留未知。体检值附 `unit` 与 `source_field`；血压拆为收缩压、舒张压并注明 `mmHg`。10 份档案的空体检对象保持 `{}`，不补零、不推断诊断。

健康目标仅做确定同义词统一：降血压→降压、控制血糖→控糖、减重→减脂。其他目标和特殊人群原样保存，后续规则明确说明支持范围，不把“健康需求”解释为确诊疾病。过敏词保持来源写法，规则层统一映射。

## 当前边界

没有可靠的成分营养库、食用份数、总耗时或设备结构字段。当前不生成精确热量、宏量营养或逐人营养摄入。复合配料仍需规则层检查已知风险及不确定性；该规范化不是过敏安全认证。词法索引只记录菜名、食材名、允许标签与类别到真实菜谱 ID 的映射，未引入向量数据库。


## 比赛 Demo 的合成画像目录

`app/infrastructure/synthetic.py` 提供 synthetic_profiles 与 load_synthetic_catalog。后者仅读取一次菜谱 CSV，不读取原始档案或对话 JSON。900001—900003 的个人字段均为手写演示值，不从真实档案派生，raw 与 measurements 为空。

UserProfile.data_scope 默认 original；合成目录明确指定 synthetic。DeepSeek 适配器只接受 synthetic，原始资料即使通过测试目录注入也会在网络请求前被阻断。Docker 构建上下文及镜像排除原始档案和原始对话目录，本地真实验收仍保留完整源数据。
