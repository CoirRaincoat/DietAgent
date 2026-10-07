"""Real recipe-library curation gates; no human/clinical/serving certification."""

import csv
import hashlib
import json

import pytest

from app.domain.preparation_enrichment import (
    AssistantCuration,
    audit_preparation,
    supplemented_steps,
    tag_enrichment,
)
from app.infrastructure.data import PROJECT_ROOT, RECIPE_PATH, normalize_recipes

REGISTRY = PROJECT_ROOT / "configs/recipe_preparation_curations.json"
RECORDS = json.loads(REGISTRY.read_text(encoding="utf-8"))["records"]


@pytest.fixture(scope="module")
def recipes():
    with (PROJECT_ROOT / RECIPE_PATH).open(encoding="gb18030", newline="") as stream:
        return normalize_recipes(list(csv.DictReader(stream)))


@pytest.mark.parametrize("record", RECORDS, ids=[r["source_name"] for r in RECORDS])
def test_authored_record_preserves_exact_source_and_no_generated_fact_is_auto_applied(
    recipes, record
):
    recipe = recipes[record["recipe_id"]]
    original = recipe.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    assert record["source_fingerprint"] == recipe.fingerprint
    assert record["source_content_sha256"] == digest
    authored = AssistantCuration.model_validate(record)
    baseline = audit_preparation(recipe)
    candidate = audit_preparation(recipe, curation=authored)
    assert candidate.findings == baseline.findings
    assert candidate.human_review == "not_reviewed" and candidate.provider_calls == 0
    assert recipe.steps in supplemented_steps(recipe, candidate, curation=authored)
    assert recipe.model_dump(mode="json") == original
    tags = tag_enrichment(recipe, candidate, curation=authored)
    assert not tags["automatically_applied"] and tags["human_review"] == "not_reviewed"
    declared = {item.name for item in recipe.ingredients}
    assert all(set(item.ingredient_names) <= declared for item in candidate.additions)
    if authored.disposition in {"retain_component", "source_conflict", "needs_confirmation"}:
        assert candidate.status == "review_required"
    if baseline.findings:
        assert not any(
            tag["dimension"] in {"cooking_method", "culinary_role"}
            for tag in tags["proposed_descriptive_tags"]
        )


def test_registry_covers_previous_raw_review_queue_and_all_ten_empty_label_gaps(recipes):
    bound_ids = {record["recipe_id"] for record in RECORDS}
    raw_review_ids = {
        recipe.recipe_id
        for recipe in recipes.values()
        if audit_preparation(recipe).status == "review_required"
    }
    assert len(raw_review_ids) == 34 and raw_review_ids <= bound_ids
    empty_names = {
        "糖桂花烤栗子",
        "面包面团（发酵）",
        "冰糖炖官燕",
        "天使白面包",
        "芝士焗龙虾",
        "小橘干",
        "冰糖烤梨",
        "芝士焗榴莲",
        "冷冻甘薯薯条",
        "肉松手撕面包",
    }
    for record in RECORDS:
        if record["source_name"] in empty_names:
            recipe = recipes[record["recipe_id"]]
            assert not recipe.raw_label.strip()
            assert not tag_enrichment(recipe, audit_preparation(recipe))[
                "tags_to_add_to_review_view"
            ]
            authored = AssistantCuration.model_validate(record)
            tags = tag_enrichment(
                recipe, audit_preparation(recipe, curation=authored), curation=authored
            )
            assert tags["tags_to_add_to_review_view"]
    assert len(bound_ids) == len(RECORDS) == 42
    assert empty_names <= {record["source_name"] for record in RECORDS}


def selected(recipes, name):
    record = next(record for record in RECORDS if record["source_name"] == name)
    recipe = recipes[record["recipe_id"]]
    authored = AssistantCuration.model_validate(record)
    return recipe, authored, audit_preparation(recipe, curation=authored)


def test_cold_noodle_draft_does_not_remove_declared_chili_or_label_it_nonspicy(recipes):
    recipe, authored, result = selected(recipes, "爽口凉皮")
    assert "油泼辣椒" in recipe.raw_ingredients
    assert "保留原配方的油泼辣椒" in supplemented_steps(recipe, result, curation=authored)
    assert all(tag.tag not in {"不辣", "清淡", "降压", "护心"} for tag in authored.tag_suggestions)


@pytest.mark.parametrize("name", ["钻石可可肉桂饼干", "椰子玛格丽特"])
def test_existing_temperature_sentence_is_clarified_not_duplicated_as_new_heat(recipes, name):
    recipe, authored, result = selected(recipes, name)
    assert result.status == "review_required"
    assert all(item.placement == "clarification" for item in result.additions)
    assert "180" in recipe.steps
    text = supplemented_steps(recipe, result, curation=authored)
    assert "【助手补全草稿·做法澄清】" in text
    assert not any(item.placement == "after_source" for item in result.additions)


def test_tag_only_curation_does_not_erase_existing_angel_bread_automatic_draft(recipes):
    recipe, authored, result = selected(recipes, "天使白面包")
    assert not authored.additions
    assert result.additions == audit_preparation(recipe).additions
    assert result.status == "draft_supplement"


def test_pork_pancake_addition_does_not_invent_new_frying_oil_or_cookedness_certificate(recipes):
    _, authored, result = selected(recipes, "猪肉大葱肉饼")
    assert result.additions
    text = "".join(item.text for item in result.additions)
    assert "不额外补煎油或水" in text
    assert "不作为内馅已熟的证明" in text
    assert any("熟化" in item for item in authored.remaining_questions)


@pytest.mark.parametrize("name", ["宫廷枣花酥", "蛋黄酥", "叉烧酥", "松子柏仁蒸鳜鱼", "黄芪汽锅鸡"])
def test_unresolved_original_formula_or_ingredient_conflicts_do_not_get_fake_complete_draft(
    recipes, name
):
    _, authored, result = selected(recipes, name)
    assert authored.disposition == "source_conflict"
    assert result.status == "review_required" and not result.additions
    assert authored.remaining_questions


@pytest.mark.parametrize("name", ["叉烧焗餐包", "蜂蜜牛奶小餐包", "酸奶小餐包"])
def test_assistant_surface_uses_are_not_misstated_as_original_reserved_purpose(recipes, name):
    recipe, authored, result = selected(recipes, name)
    # These exact raw recipes do not say the source reserved surface ingredients.
    assert "预留" not in recipe.steps
    text = authored.reason + "".join(item.text for item in result.additions)
    assert "为表面预留" not in text and "清单中用于表面" not in text
    assert "表面全蛋液余量" not in authored.reason
    if name != "酸奶小餐包":
        assert "建议" in text


def test_citrus_curation_reason_matches_source_segments_not_invented_slices(recipes):
    recipe, authored, _ = selected(recipes, "小橘干")
    assert "沃柑" in recipe.steps and "小瓣" in recipe.steps
    assert "分瓣去籽并摆架" in authored.reason and "切片" not in authored.reason


def test_herbal_chicken_reason_does_not_invent_source_vessel_loading(recipes):
    recipe, authored, _ = selected(recipes, "黄芪汽锅鸡")
    assert "姜" in recipe.steps and "葱" in recipe.steps
    assert "止于食材准备" in authored.reason and "装料" not in authored.reason
