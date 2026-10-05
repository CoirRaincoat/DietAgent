"""Synthetic counterexamples for corrected archive normalization and retrieval."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_agent_api import ScriptedLLM, complete_intent
from test_agent_api import catalog as catalog

from app.agent.service import MealAgent
from app.api.main import create_app
from app.domain.models import Constraints, Intent
from app.infrastructure.data import normalize_recipes
from app.infrastructure.recipe_metadata import SQLiteRecipeMetadata, write_metadata
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.normalization.normalizer import TermNormalizer
from app.retrieval.core import RetrievalUnavailable, VectorHit
from app.retrieval.hybrid_retriever import HybridRetriever
from app.schemas.recipe_meta import RecipeMeta
from app.tools.recipe_search import RecipeSearchTool
from scripts.etl_recipes import build_metadata


class Ranker:
    def __init__(self, hits=(), failure=False):
        self.hits = list(hits)
        self.failure = failure
        self.calls = []

    def rank(self, query_terms, *, candidate_ids):
        self.calls.append(tuple(candidate_ids))
        if self.failure:
            raise OSError("synthetic ranker failure")
        return self.hits


class Metadata:
    def __init__(self, records=(), failure=False):
        self.records = list(records)
        self.failure = failure

    def select(self, *, required_labels):
        if self.failure:
            raise OSError("synthetic metadata failure")
        return self.records


@pytest.mark.parametrize("raw,canonical", [
    ("对鱼过敏", "鱼"), ("不能吃海鲜", "海鲜"),
    ("忌吃花生", "花生"), ("不吃豆制品", "豆制品"),
    ("对牛奶过敏", "牛奶"), (" FISH ", "fish"),
])
def test_normalizer_preserves_allergen_meaning_and_raw_evidence(raw, canonical):
    result = TermNormalizer().normalize_terms([raw], kind="allergy")
    assert result.known_terms == [canonical]
    assert result.unknown_terms == []
    assert result.evidence[0].raw == raw
    assert result.evidence[0].canonical == canonical


@pytest.mark.parametrize("raw,canonical", [
    ("西红柿", "番茄"), ("马铃薯", "土豆"), ("芫荽", "香菜"),
])
def test_normalizer_uses_existing_food_aliases(raw, canonical):
    assert TermNormalizer().normalize_terms([raw]).known_terms == [canonical]


@pytest.mark.parametrize("raw", [
    "新的未知食材", "对未知食材过敏", "鱼和未知食材", "对鱼过敏吗？",
    "如果对鱼过敏", "没有鱼过敏", "对鱼可能过敏", "鱼，未知食材",
])
def test_unknown_or_uncertain_phrase_never_becomes_a_partial_safe_match(raw):
    result = TermNormalizer().normalize_terms([raw], kind="allergy")
    assert result.known_terms == []
    assert result.unknown_terms == [raw]
    assert result.evidence[0].canonical is None


def test_normalizer_preserves_each_unknown_and_deduplicates_only_values():
    result = TermNormalizer().normalize_terms(["芫荽", "香菜", "未知甲", "未知乙", "未知甲"])
    assert result.known_terms == ["香菜"]
    assert result.unknown_terms == ["未知甲", "未知乙"]
    assert len(result.evidence) == 5


def test_metadata_does_not_invent_nutrients_or_safety_review(catalog):
    source = catalog.recipes["test_0"].model_copy(update={
        "ingredients": [*catalog.recipes["test_0"].ingredients,
                        catalog.recipes["test_0"].ingredients[0].model_copy(update={"name": "未知配料"})],
    })
    record = RecipeMeta.from_recipe(source)
    assert record.recipe_id == source.recipe_id
    assert (record.fingerprint, record.source_row) == (source.fingerprint, source.source_row)
    assert record.unknown_ingredients == ["未知配料"]
    assert record.safety_review == "not_reviewed"
    assert not {"calories", "protein_g", "servings", "cooking_minutes"} & record.model_dump().keys()
    with pytest.raises(ValidationError):
        RecipeMeta.model_validate(record.model_dump() | {"calories": 123.0})
    with pytest.raises(ValidationError):
        RecipeMeta.model_validate(record.model_dump() | {"safety_review": "validated"})


def test_empty_metadata_never_runs_unfiltered_vector_search(catalog):
    ranker = Ranker([VectorHit("test_0", 100)])
    retriever = HybridRetriever(catalog.recipes.values(), metadata=Metadata(), ranker=ranker)
    assert retriever.search([], Constraints()) == []
    assert ranker.calls == []


@pytest.mark.parametrize("stage", ["metadata", "ranker"])
def test_backend_failure_is_explicit_and_does_not_relax_filters(catalog, stage):
    ranker = Ranker(failure=stage == "ranker")
    retriever = HybridRetriever(
        catalog.recipes.values(), metadata=Metadata(failure=True) if stage == "metadata" else None,
        ranker=ranker,
    )
    constraints = Constraints(allergies=["鱼"], no_spicy=True)
    before = constraints.model_dump()
    with pytest.raises(RetrievalUnavailable):
        retriever.search([], constraints)
    assert constraints.model_dump() == before
    if stage == "metadata":
        assert ranker.calls == []


@pytest.mark.parametrize("constraints", [
    Constraints(allergies=["未知过敏原"]),
    Constraints(excluded_ingredients=["未知忌口"]),
    Constraints(inventory=[]), Constraints(max_minutes=20),
])
def test_unverifiable_hard_constraints_prevent_vector_calls(catalog, constraints):
    ranker = Ranker([VectorHit("test_0", 10)])
    assert HybridRetriever(catalog.recipes.values(), ranker=ranker).search([], constraints) == []
    assert ranker.calls == []


def test_vector_ids_are_intersected_with_safe_catalog_and_deduplicated(catalog):
    ranker = Ranker([
        VectorHit("not-in-catalog", 10000), VectorHit("test_2", 1000),
        VectorHit("test_9", 500), VectorHit("test_3", 10),
        VectorHit("test_3", 9), VectorHit("test_0", float("nan")),
    ])
    constraints = Constraints(allergies=["海鲜"])
    retriever = HybridRetriever(catalog.recipes.values(), ranker=ranker)
    result = RecipeSearchTool(retriever)([], constraints)
    ids = [recipe.recipe_id for recipe in result]
    assert ids[0] == "test_3"
    assert len(ids) == len(set(ids))
    assert not {"test_2", "test_9", "not-in-catalog"} & set(ids)
    assert not {"test_2", "test_9"} & set(ranker.calls[0])
    assert all(recipe is catalog.recipes[recipe.recipe_id] for recipe in result)
    # limit=None retains the entire safe pool, not just vector hits.
    assert len(ids) == len(catalog.recipes) - 2


def test_stale_metadata_cannot_map_to_another_source_recipe(catalog):
    record = RecipeMeta.from_recipe(catalog.recipes["test_0"])
    stale = record.model_copy(update={"fingerprint": "stale"})
    ranker = Ranker([VectorHit("test_0", 99)])
    retriever = HybridRetriever(catalog.recipes.values(), metadata=Metadata([stale]), ranker=ranker)
    assert retriever.search([], Constraints()) == []
    assert ranker.calls == []


def test_metadata_cannot_admit_ineligible_or_label_mismatched_source(catalog):
    record = RecipeMeta.from_recipe(catalog.recipes["test_0"])
    forged = record.model_copy(update={"labels": ["不存在的标签"], "eligible": True})
    ranker = Ranker([VectorHit("test_0", 1)])
    retriever = HybridRetriever(catalog.recipes.values(), metadata=Metadata([forged]), ranker=ranker)
    assert retriever.search([], Constraints(), required_labels=["不存在的标签"]) == []
    assert ranker.calls == []


@pytest.mark.parametrize("limit", [0, -1])
def test_retrieval_limit_contract(catalog, limit):
    ranker = Ranker()
    retriever = HybridRetriever(catalog.recipes.values(), ranker=ranker)
    if limit < 0:
        with pytest.raises(ValueError):
            retriever.search([], Constraints(), limit)
    else:
        assert retriever.search([], Constraints(), limit) == []
    assert ranker.calls == []


def test_sqlite_cache_preserves_source_identity_and_refuses_overwrite(tmp_path, catalog):
    record = RecipeMeta.from_recipe(catalog.recipes["test_0"])
    path = tmp_path / "derived cache #中文.db"
    assert write_metadata(path, [record]) == 1
    before = path.read_bytes()
    assert SQLiteRecipeMetadata(path).select(required_labels=[]) == [record]
    assert path.read_bytes() == before
    with pytest.raises(FileExistsError):
        write_metadata(path, [])
    assert path.read_bytes() == before
    assert SQLiteRecipeMetadata(path).select(required_labels=["不存在"]) == []


def test_missing_sqlite_cache_is_never_created_by_search(tmp_path, catalog):
    path = tmp_path / "missing.db"
    retriever = HybridRetriever(catalog.recipes.values(), metadata=SQLiteRecipeMetadata(path))
    with pytest.raises(RetrievalUnavailable):
        retriever.search([], Constraints())
    assert not path.exists()


def test_cache_build_failure_removes_only_new_file(tmp_path, catalog):
    record = RecipeMeta.from_recipe(catalog.recipes["test_0"])
    path = tmp_path / "new.db"
    with pytest.raises(sqlite3.IntegrityError):
        write_metadata(path, [record, record])
    assert not path.exists()


def test_etl_uses_original_csv_encoding_ids_and_preserves_source(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text(
        '名称,食材清单,烹饪步骤,label\n合成番茄菜,主料：西红柿100克；未知甲少许,煮熟,晚餐\n',
        encoding="gb18030",
    )
    before = source.read_bytes()
    output = tmp_path / "derived.db"
    assert build_metadata(source, output) == 1
    records = SQLiteRecipeMetadata(output).select(required_labels=["晚餐"])
    assert len(records) == 1
    normalized = normalize_recipes([{
        "名称": "合成番茄菜", "食材清单": "主料：西红柿100克；未知甲少许", "烹饪步骤": "煮熟", "label": "晚餐",
    }])
    recipe = next(iter(normalized.values()))
    assert (records[0].recipe_id, records[0].fingerprint) == (recipe.recipe_id, recipe.fingerprint)
    assert records[0].unknown_ingredients == ["未知甲"]
    assert json.loads(records[0].model_dump_json())["safety_review"] == "not_reviewed"
    assert source.read_bytes() == before
    with pytest.raises(ValueError):
        build_metadata(source, source)
    assert source.read_bytes() == before


async def test_optional_retriever_is_usable_through_agent_tools(tmp_path, catalog):
    ranker = Ranker([VectorHit("test_3", 1)])
    agent = MealAgent(
        catalog, SessionStore(tmp_path / "state.db"),
        ScriptedLLM([complete_intent(allergies=["海鲜"])]),
        retriever=HybridRetriever(catalog.recipes.values(), ranker=ranker),
    )
    result = await agent.chat(3, "1人晚餐，对海鲜过敏")
    assert result.status == "ok"
    assert any(event.name == "recipe_search" for event in result.tool_calls)
    assert not {"test_2", "test_9"} & {item.recipe_id for item in result.menu}
    assert ranker.calls


def test_default_agent_keeps_lexical_retrieval(tmp_path, catalog):
    from app.retrieval.keyword import KeywordRetriever

    agent = MealAgent(catalog, SessionStore(tmp_path / "state.db"), ScriptedLLM())
    assert isinstance(agent.retriever, KeywordRetriever)


@pytest.mark.parametrize("endpoint,stream", [
    ("/chat", False), ("/v1/chat/completions", False), ("/v1/chat/completions", True),
])
def test_optional_backend_failure_returns_503_and_retains_new_restrictions(
    tmp_path, catalog, endpoint, stream,
):
    ranker = Ranker()
    retriever = HybridRetriever(catalog.recipes.values(), ranker=ranker)
    settings = Settings(_env_file=None, deepseek_api_key="", session_db=tmp_path / "state.db")
    store = SessionStore(settings.database_path)
    llm = ScriptedLLM([complete_intent(), Intent(allergies=["花生"])])
    with TestClient(create_app(settings, llm, catalog, store, retriever=retriever)) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有忌口"}).json()
        assert first["status"] == "ok"
        session = first["conversation_state"]["session_id"]
        ranker.failure = True
        if endpoint == "/chat":
            body = {"user_id": 3, "message": "新增花生过敏", "session_id": session}
        else:
            body = {
                "model": "fangtai-meal-agent", "messages": [{"role": "user", "content": "新增花生过敏"}],
                "user": "3", "session_id": session, "stream": stream,
            }
        result = client.post(endpoint, json=body)
    assert result.status_code == 503
    assert "synthetic ranker failure" not in result.text
    assert "限制仍然保留" in result.text
    if endpoint == "/chat":
        assert result.json()["detail"]["code"] == "RETRIEVAL_UNAVAILABLE"
    else:
        assert result.json()["error"]["code"] == "retrieval_unavailable"
        assert "text/event-stream" not in result.headers["content-type"]
    saved = store.get(session, 3)
    assert "花生" in saved.constraints.allergies
    assert not saved.menu_valid
