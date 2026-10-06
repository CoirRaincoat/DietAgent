# 整餐饮食模式：执行与人工复核

2026-10-01，本地完成有限源文字执行与验收，尚未推送/合并。长任务仍继续。

## 机制

`diet_mode` 与非汤、非主食荤素数量分开：蛋奶素不含肉、鱼虾、肉汤，允许蛋奶蜂蜜；纯素还排除蛋奶蜂蜜等动物来源。汤、主食、原料、做法新增及替换建议全部参与门禁，未知复合配料/蘸料和解析不完整不能由“未发现肉”转为素食证明。明确0荤只限制荤菜位，不自动转整餐素食。

原文有限解析补齐模型遗漏，但不接受没有原文依据的模型模式猜测。个人模式归属参餐者；在场成员限制聚合共享菜单，退出时事实保留而不贡献，恢复后生效。含糊“吃素”、冲突或称呼不清先澄清，重启/重试不丢失。连词、只给某人、群体/朋友、历史/引用/解释有独立反例。明确取消整餐模式不撤销过敏、排除食材或禁辣。指定单槽却要求改变其他菜位时拒绝偷偷扩范围，请求全餐调整许可；旧过敏跨槽修复合同保持原样。

执行入口：`app/agent/diet_mode.py`、`app/rules/diet.py`、`app/domain/dish_composition.py`；状态、规划和解释分别接在 models、diners、service、planner、response_copy。解释强制加入源核验与未测边界。

## 独立验收

`evaluation/diet_oracle.py` 不导入生产规则/分类器，使用开发者按源原料/步骤编写的ID审核表；汤、主食、建议同样检查，不接受服务自报纯素。结合源ID/指纹/原料步骤核验、独立不辣与正餐反例门禁；饮食检查失败不进入菜单质量观察。公共验证器v13，整餐oracle v1、正餐反例oracle v7；原v3字节及历史报告保留。

源回放发现玫瑰卷步骤添加未知蘸料、银耳蛋汤含冰糖40g但因鸡蛋的“鸡”被误认为咸食；新增红测后修复，咸味/肉汤对照不误拒。未知蘸料不会静默删掉冒充原菜。

## 证据

- 本地提交：`fdfccd8` 执行；`3b55371` 独立验收；`890a3ec` 源反例；`93947f6` 连词及称呼归属。基线 `debfe0d`。
- 374专项（110模式行为+13独立验收+10新增源反例+239既有数量+2酸梅汤），1304全量通过，1条既有TestClient警告。严格类型检查、新模块Black、全仓Ruff通过。
- 模式原文语句102/103（99.03%），语句/分支综合口径98%；源身份、饮食门禁、独立饮食oracle语句/分支100%，未覆盖防御分支未排除。
- 同源CSV与输入：5个直接规划+5组确定性HTTP会话，基线缺少新模式字段；本次不调用真实生成模型。直接回放基线5个都不通过整餐源核对；候选4个给出合规菜单，另1个无足够已核验正餐汤，**不是5/5通过**。HTTP候选4/5通过来源、禁辣、正餐和饮食门禁；基线两条纯素请求澄清，其余整餐请求肉汤漏检，不混同直接规划口径。
- 7道含2蛋奶素汤：当前有限审核范围内只有1道合规正餐汤，真实返回候选不足，不换成甜汤、不少给一道再声称满足。需继续审核召回，不能外推“全库无解”。
- 旧0荤菜单追加整餐蛋奶素：猪肉汤换田园蔬菜汤，其他菜保留。纯素回放移除鸡蛋，源核对包含汤。

人工复核包：本地 `runtime/whole_diet_human_review/20260930T222549Z/`。`report.md` 便于阅读；`report.json` 给出源码/输入/CSV/审核表哈希、版本、环境与限制；`comparisons.jsonl` 含前后菜单和HTTP完整请求/返回；`examples.jsonl`391条输入（374开发用例+12手写菜谱+5源请求）；`test_source_snapshots.json` 测试全文；374/1304逐项结果及中间失败另存JSONL。覆盖记录 `runtime/whole-diet-finish-coverage.json`。早期报告保留，不改写已发现错误的绿灯。

## 复测

在本仓库运行，使用现成Python环境；以下路径为已存在本地环境，不是用户机器的通用安装约定：

```powershell
New-Item -ItemType Directory -Force .\runtime | Out-Null
& .\runtime\fix-test-env\Scripts\python.exe -m pytest tests/test_whole_meal_diet.py tests/test_diet_oracle.py tests/test_diet_source_counterexamples.py tests/test_dish_composition.py tests/test_sour_plum_meal_role.py -q -p no:cacheprovider --basetemp .\runtime\pytest-diet-check
& .\runtime\fix-test-env\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .\runtime\pytest-diet-full
```

`--basetemp` 必须是专用测试目录，pytest可能清空该目录；不要指向已有报告或项目根目录。Docker本轮未重新验证，不把Windows测试当Docker证据。

## 尚未完成

仅核对菜谱声明文字：有限食材/步骤词表不保证品牌完整配方、未列食材或交叉接触；保守来源检查可能降低召回，未做全库审核。本轮是开发集而非官方20组或独立留出，也不是人工盲评校准。

源菜单仍有水煮蛋、盐1大勺、纯素蛋白质主角色缺口；未证明成人晚餐适配、降压护心或份量/营养达标。规划5次计时有升有降，旧菜单追加模式中位数69.02→81.08ms，其他不能与失败早退计时混作速度提升；非TTFT。未新增AI Judge、真实模型理解/性能或人工评分，不给质量满分。

下一项：成人共享正餐/餐次适配，再健康目标及追加软要求、跨餐重复；继续版本化报告。
