# 压缩包扩展的融合范围

2026-10-05。来源为用户提供的 DietAgent.7z，包内 Git 基线 `612ca3a`，附件 SHA-256 为 `b1bf31bc244ef0ed86ac682b19ddd29be63e5353341c850c421e5d3adeaf6b8a`。只提炼源码设计，不导入附件环境、配置、密钥或 Git 工作区。

## 已整合

- `TermNormalizer.normalize_terms` 返回已知词、完整未知词和逐项原文证据。词典统一使用 `configs/rules.yaml`；保留海鲜的宽泛分组语义。只接受有依据的精确单词和短语，不用向量近似确认过敏安全。
- `RecipeMeta` 是从现有 `Recipe` 派生的入库 DTO，保留 `recipe_id/fingerprint/source_row`，不建立第二套领域菜谱模型。`safety_review=not_reviewed`，结构校验不等于安全审核。
- `RecipeRetriever` 是工具层唯一检索接口，返回现有 `Recipe` 对象。默认词法检索不变；`HybridRetriever` 可以通过依赖注入使用元数据和候选排序端口。
- 可选检索先核对本地来源、标签和硬约束，再排序已允许的 ID，最后复查硬约束。零候选保持为空；后端出错明确返回 `RetrievalUnavailable`，两个 API 都以 HTTP 503 响应，保留本轮已保存的限制。
- 原附件两份检索实现已收敛；SQLAlchemy/Qdrant/Chroma 客户端和重复词典没有进入默认运行依赖。

## 本地派生缓存

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.etl_recipes --output artifacts/recipe_metadata.db
```

该命令只读取公开 GB18030 菜谱 CSV，创建新的 SQLite 派生缓存；输出已存在时失败，不重建已有索引或覆盖来源。没有模型、下载、向量服务或 hash 伪 embedding。

可选接入示例：

```python
from pathlib import Path
from app.api.main import create_app
from app.infrastructure.synthetic import load_synthetic_catalog
from app.infrastructure.recipe_metadata import SQLiteRecipeMetadata
from app.retrieval.hybrid_retriever import HybridRetriever

catalog = load_synthetic_catalog()
retriever = HybridRetriever(
    catalog.recipes.values(),
    metadata=SQLiteRecipeMetadata(Path("artifacts/recipe_metadata.db")),
    # ranker=... 需要另行实现并验收真实语义排序；不自动创建外部客户端。
)
app = create_app(catalog=catalog, retriever=retriever)
```

没有真实语义后端的收益证据，因此默认应用不启用向量召回；端口只提供可测试的扩展边界。

## 舍弃或延期

- 舍弃 `openai_compat.py` 中错误模块名和装饰器拼写，保留正常 JSON/SSE 接口。
- 附件的 DeepSeek 流式解析、400 token 默认值及 finish_reason 放宽没有导入；沿用严格非流式解析与截断诊断。真正的 SSE 增量适配需独立实现和验收。
- 不导入无份量/来源证明的热量、份数和耗时字段；不把结构化或近似匹配当作营养/健康证据。
- 不导入原 JSONL 的自由 LLM 抽取入库和自动向量集合重建。将来扩展必须继续关联原来源身份。

## 本轮验证

40 项合成反例全部通过，覆盖未知词保留、海鲜语义、空过滤、后端失败、非法向量 ID、来源指纹变化、标签不匹配、缓存只读、输出防覆盖、工具接入及 JSON/SSE 503。公开 CSV 派生缓存包含 2000 条源记录，其中 1971 条可推荐记录的身份与来源核对通过。
