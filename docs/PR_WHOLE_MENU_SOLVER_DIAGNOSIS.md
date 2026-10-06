# eval: 整餐约束求解原型、保基线回放与健康菜单卡点诊断

## 结果先行

实现了离线整餐联动与失败定位，**没有证明实际菜单改善，不默认接入**。代码本地提交 `0de11a1`，基线 `04c36ea`；不自动推送／合并，不改app/configs、原2000配方或生产依赖。

## 改动

- 自行建立整餐槽位布尔模型，依赖固定OR-Tools9.15.6755，只在隔离runtime环境。经来源／安全／同角色／餐次／查询／身份必要门禁后保完整peer池，原菜始终保留；全餐授权才可联动3＋槽，局部冻结其他槽。
- 约束唯一ID／名称、汤数及明确荤素配额、逐目标非退步、规则参考总值、已覆盖食材／来源类型／场景／口味／指定方法、警示频次、食材／主体／做法集中。额外方法与冷热的坐标保护比最终lex更保守，范围明示，不称搜索了所有原库菜单。
- 基线可行性回放使用真实固定约束，hint不是fallback证明。实际候选完整记录再由独立门禁核验；模型错误、限时、门禁拒绝均保基线，未知不是不可解。墙钟预算覆盖建模／求解，阶段检查不抢占Python特征提取，实际时间非TTFT。
- 独立无优化、单线程假设可行性检查，所有守护仍断言，不自动放松。输出足够假设子集（非最小／因果排名）；候选域加总上界忽略跨槽限制。真实高参考单菜与原步骤及拒绝项供人工检查，不能视为推荐或临床更好。
- 可复现报告CLI；生成JSON／JSONL／MD／静态HTML／空人工标签／材料SHA。不发送原库、官方／私有或密钥给GitHub／模型。

## 实际对照

20**复用开发条件**：12直接条件＋8旧接口种子，18原2000／2公开自编，不是独立留出或官方20。最终56菜单调用＋4诊断：

- 19支持基线固定模型OPTIMAL，1不支持请求保原样。
- 4健康输入各全餐／单槽／两槽／指定局部／同输入复跑，20次均守护域内严格改善INFEASIBLE，原菜单保留。
- 15无配置健康请求不扰动，2不支持调用（同1条件的两模式）。全20条件菜单变化0。
- 家庭直接健康的足够子集：known_method_dishes＋method_concentration；健康接口另涉警示频次／主体重复／逐目标／方法种类，纯素known_method_dishes。单人子集空，其固定候选域忽略跨槽上界8等于原8。
- 不是全库／全局无解，也不是放松这些代理就能证明更好。下一分清用户明确要求与内部方法频次守护，再按真实菜单实用性验证，不再扩池求点数。

最终封存入口：`runtime/whole_menu_solver_review/20261004-sealed/report.html`；同目录MD／JSON／JSONL、20空人工意见及28材料SHA。报告含原源配方，只本地。静态HTML未浏览器视觉验收。首版56、中版56＋4、封存56＋4共176重复源调用记录，不当176独立样本；前版manifest指向的源码后续可能变化，最终封存及源码归档为权威。

## 验证

- 36新合同在隔离求解环境通过：三槽补偿、1/2槽及局部拒绝、穷举对照、固定基线、不辣／过敏／明确排除、汤数／荤素／偏好／来源篡改、限时／重放、解释及上界。
- 主环境4287通过、1求解测试模块跳过、1旧Starlette警告，93.40s；新36另环境通过，不声称同环境4323绿测。隔离pytest有asyncio_mode未知配置警告。
- 初始缺求解模块红测、首版2失败／24通过保留：木耳夹具无有限表示而被正确拒绝，改为可观察番茄；没有放宽门禁／补入不存在的食材特征。
- 4新文件Ruff／Black、3新模块strict通过，不是全项目类型认证。21解析依赖散列固定、pip check通过，服务env无OR-Tools。

## 来源与许可

[OR-Tools v9.15完整源commit](https://github.com/google/or-tools/commit/551ad10d94835c99e5e1e684500d3db398c0e345)：`551ad10d94835c99e5e1e684500d3db398c0e345`。本项目自行建模，没有复制其算法源码。已安装完整Apache2.0许可读取并保留，21wheel版本／SHA锁定于 `evaluation/requirements_solver_win_py312.txt`。传递依赖全许可和wheel构建源commit的可复现证明尚未全核，不准直接作为生产发行完成。

## 复现

现有隔离环境的命令（先进入本worktree；输出必须用不存在的新目录）：

    .\runtime\whole-menu-solver-env\Scripts\python.exe -m pytest tests/test_whole_menu_solver.py -q -p no:cacheprovider --basetemp runtime/pytest-whole-solver-new

    .\runtime\whole-menu-solver-env\Scripts\python.exe -m evaluation.run_whole_menu_solver --source dataset/recipe_kb/recipes_sample_2000.csv --source-snapshot runtime/light-full-preparation-after-final.json --api-snapshot runtime/light-full-preparation-api-after-final.json --conditions runtime/health-reference-pairs-20261004.json --output runtime/whole_menu_solver_review/new-run --seconds 30

三个输入快照是本地原有开发材料、不入Git。CLI校验原CSV字节SHA和完整规范化目录、原种子记录；不接受未知scope或覆盖已有报告，不把旧接口回执当新HTTP成功。requirements仅针对Windows x64／CPython3.12；其他平台须另锁wheel。自行创建隔离环境、安装 `--require-hashes -r evaluation/requirements_solver_win_py312.txt` 需要网络且不得装到服务环境。

## 验收边界／未完成

原菜库byteSHA与完整规范化记录未改。0新HTTP／模型API／真人／真实NLU／TTFT；没有独立AI或人体营养认证，20人工意见全空。定性源目标／分类不等于份量、蛋白质克数、盐油量、降压护心疗效或质量评分。安全、明确需求和局部范围不放宽；原健康菜单及同质化实际问题仍未解决。

旧清淡公开5组双序付费授权未答，仅精确公开包可待新许可，不扩到本实验；不给DeepSeek完整报告。本solver、旧多样性、健康类别／初始前沿默认OFF。独立新60／真人20–30、官方私有绑定、真实生成性能、协作者PR差异与最终创新总结仍待推进；总体目标继续active。
