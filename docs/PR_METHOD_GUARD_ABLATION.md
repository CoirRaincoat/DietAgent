# eval: 区分做法需求与内部频次守护，并保留无净收益对照

## 结果

代码 `3c820d2`，基线 `1dc14a4`，本地提交、未推送／合并。新增的是离线方法对照及证据缺口定位，**20复用条件没有实际菜单改善，不接入默认服务**。app/configs、原2000字节、服务依赖及OR-Tools解析锁未改。

## 单主题变化

旧 `strict_baseline` 仍默认：方法种类／频次／重复和完整balance都保原值。新 `capped_balance` 仅显式evaluation试验，沿用现有排菜最多3种方法目标、已知方法菜数和基本冷热前缀；内部每种频次允许有界取舍，集中上限与重复上界明确输出。不等于用户要少做法，也不是对“做法多样”实用性的金标。

具体方法、scoped食材／菜位、用户明确汤／荤素数量、已覆盖食材／口味／场景／来源类型、逐项目标、警示頻次、声明食材／主体重复、餐次／同角色／查询／身份、原过敏／不辣／素食及局部权限均沿用。更多方法未知不能补奖；负向、冲突和更具体未知句仍拒绝保持原样，不默默当无需求。独立原记录核验分别按本策略、旧严格策略输出，不把旧内部rank取舍隐藏为“安全全部通过”。

## 同源对照

20复用开发条件＝18原2000／2公开自编，不是独立留出／官方20。80菜单调用＝56严格＋4健康输入×6 capped；另8独立无优化单线程可行性诊断。23固定基线可行，40严格健康改善调用INFEASIBLE保基线，15无健康不扰动，2不支持来自同条件两模式；所有菜单变化0。完整池仅指受护peer域，没有证明全库无解。

直接健康家庭在capped下足够假设子集为声明食材重复＋已知方法；健康接口仍涉警示／主体重复／逐目标／方法种类，纯素已知方法，单人仍前置域。不保证最小／因果排名，不把任何约束自动撤销。实验证明“仅保3种并允许频次变化”不足以改善当前源菜单。

只读原库审计：三个非纯素健康输入各125条相同ID集合，union125；有源成菜动作、有限肉鱼主体参考和餐次成本资格，但旧focus或declared-family缺项。审计如柠汁煎鳕鱼等原完整配方／动作／观察保留，**没有认证最终peer、需求、品牌、份量或临床适合性，也没有自动放行125条**。下一核完整声明表示／真正主体关系，不把鱼汤、肉碎调味、复合酱或泛称当主菜事实。

香蒸贝贝南瓜只有未知设备程序的“开始烹饪”，不靠菜名补蒸；冬瓜双豆最后“抄拌”具体成菜动作未知，不改写原步骤来赚分。

## 报告与验证

简明人工入口 `runtime/method_policy_review/20261004-sealed/summary.html`（同目录summary.md）。完整原菜单、各模式、原配料／步骤、方法界限／旧门禁违反项在report.md/json/records.jsonl；20人工意见全空、27材料SHA、前后代码归档与125来源缺项审计。初版与封存共176重复调用（各80＋8），不是176独立案例；历史manifest指向已变源码时以归档／最终manifest为准。静态HTML未浏览器视觉验收。

- 29新合同＝10纯方法界限＋19求解：方法目标不全蒸、具体／scoped要求、未知／否定／更具体语句、原步骤未知、过敏／不辣／排除／纯素、局部范围、原输入不变与重复回放。
- 隔离专项65＝旧36＋新29通过，asyncio_mode配置警告另记；主环境4297通过＋2求解模块跳过、1旧Starlette警告，96.94s，新10纯逻辑包含在主环境。不能相加伪装单环境更多绿测／质量样本。
- 首跑仅pytest保留参数名request收集错误，XML保留；改名preference，不删合同、不改算法门槛。
- 6文件Ruff/Black、4新／改模块strict通过，非全项目类型认证。最终80菜单调用实际耗时0.000167–0.936352s，含准备／建模；非TTFT／模型生成SLA或完整2000任意搜索能力。
- 0新HTTP／付费模型／真人／真实NLU／TTFT。原CSV SHA、完整规范化源与旧种子记录均核对，未知不补做法／医学标签；没有本轮外部下载／新依赖安装，沿用隔离OR-Tools环境。

## 复现

先进入本worktree，输出目录必须不存在，不得用含用户材料的目录作basetemp：

    .\runtime\whole-menu-solver-env\Scripts\python.exe -m pytest tests/test_method_guard_policy.py tests/test_method_policy_solver.py tests/test_whole_menu_solver.py -q -p no:cacheprovider --basetemp runtime/pytest-method-policy-new

    .\runtime\whole-menu-solver-env\Scripts\python.exe -m evaluation.run_whole_menu_solver --source dataset/recipe_kb/recipes_sample_2000.csv --source-snapshot runtime/light-full-preparation-after-final.json --api-snapshot runtime/light-full-preparation-api-after-final.json --conditions runtime/health-reference-pairs-20261004.json --output runtime/method_policy_review/new-run --seconds 30 --method-policy-ablation

未传ablation开关仍只跑原严格模式；三个快照是本地开发材料，不入Git。输入校验、既有输出拒绝覆盖和静态转义报告不变。缺OR-Tools时求解测试模块明示跳过，不把跳过记通过。

## 边界与下一项

定性源参考、分类、工程分不是营养量、盐油量、疗效或质量评分；本轮0实用性净收益，所有可选方法／多样性／类别／初始前沿／solver继续OFF。下一修共享完整食材词表与主体候选资格，先反例／来源验证再同源菜单、人类复核，不通过泛化关键词绕过安全或原需求。总体active，健康家庭披萨／大份肘子与单人同质化未验收。

报告含原配方仅本地。旧清淡5组公开×双序新费用授权未答、不覆盖本主题；不复用耗尽额度。独立新60、真人20–30、官方私有绑定、可靠份量成分、真实生成性能、协作者PR差异和最终创新总结继续保留，不因更多绿测宣称完成。
