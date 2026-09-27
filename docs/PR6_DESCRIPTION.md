# PR6：接入私有档案 × 原始对话矩阵回放

## 目的

在 PR5 的报告写入机制上接入本地私有验收数据。原始 20 组对话没有 `user_id` 或映射文件，因此将 50 个档案 ID 与 20 组对话交叉回放，每组配对使用独立会话：预期 1000 会话、1450 轮。

## 改动

- 增加 `evaluation.private_matrix`：严格校验 50 档案、20 对话、29 原始轮次、2000 菜谱行，以及与人工 Intent 标注匹配的对话 SHA-256。
- 复用 PR5 报告写入层，保留公开合成回归的原有文件格式；私有入口输出 `report.json`、`report.md`、`failures.jsonl`。
- 私有报告只记录数值 ID、状态、耗时、失败代码和源文件 SHA-256。写入前检查字段白名单；原始健康资料、对话文本、完整响应和密钥不进入报告。
- 为矩阵构造、输入拒绝、会话隔离、覆盖汇总和脱敏输出增加测试，补充运行文档。

## 验证与限制

- 已核对本地原始文件结构：50 个唯一档案 ID、20 组对话、29 轮、无对话 `user_id`；菜谱 CSV 为 2000 行。
- 静态语法编译、Ruff 检查和 `git diff --check` 已通过。
- 完整 pytest 与 1000 会话基线需在有 Python 3.11 虚拟环境的宿主机执行；运行命令见 [私有矩阵文档](PRIVATE_MATRIX.md)。基线结果应在本 PR 描述中补充，不能把未运行项写成通过。
- 意图来自固定人工 fixture，此回放验证规则与会话工程行为，不测真实模型的自然语言理解或首 Token 延迟。没有菜单的澄清场景不计菜谱来源和过敏核验；源菜谱文字核验无法保证品牌配方、交叉接触或定量营养。
- 本矩阵用于覆盖分析，不代表官方已经把每组对话指定给全部 50 个用户。报告不提供评委评分。

## 推送前命令

```powershell
New-Item -ItemType Directory -Force runtime | Out-Null
python -m pytest tests/test_private_matrix.py tests/test_regression_suite.py -q `
  -p no:cacheprovider --basetemp runtime/pytest-pr6
```

运行私有矩阵并查看 `runtime/private_matrix_reports/` 下最新的 `report.md`；如报告有失败，将失败代码作为后续独立 PR 的依据，不修改本 PR 的验收事实。
