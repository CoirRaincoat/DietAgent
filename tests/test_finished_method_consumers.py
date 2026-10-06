"""Authored source actions, not catalog method labels, drive culinary consumers."""

import pytest

from app.agent.menu_balance import analyze_menu_balance, balance_summary, serving_temperature
from app.agent.menu_diversity import menu_similarity_penalty
from app.api.presentation import build_card
from app.domain.models import Ingredient, Recipe
from app.infrastructure.data import normalize_recipes
from evaluation.menu_quality import menu_quality_snapshot, summarize_menu_quality


def dish() -> Recipe:
    """Make synthetic steamed patties with misleading equipment/prep labels."""
    return Recipe(
        recipe_id="authored-steam-patty",
        name="鸡肉蔬菜饼",
        raw_ingredients="鸡肉、白菜",
        ingredients=[Ingredient(raw="鸡肉", name="鸡肉"), Ingredient(raw="白菜", name="白菜")],
        steps="鸡肉切碎，拌匀放入烤肠模具。再放入蒸锅，蒸15分钟，取出装盘。",
        methods=["蒸", "煮", "拌", "烤"],
        categories=["protein"],
        source_row=1,
        fingerprint="authored-source-only",
    )


def test_menu_summary_does_not_claim_equipment_or_preparation_diversity() -> None:
    balance = analyze_menu_balance([dish()])
    assert balance.method_counts == {"蒸": 1}
    assert balance_summary(balance).endswith("做法有蒸。")


def test_card_badges_do_not_present_equipment_as_actual_baking() -> None:
    card = build_card(dish())
    assert "蒸" in card.badges
    assert "拌" not in card.badges and "烤" not in card.badges and "煮" not in card.badges


def test_missing_directory_tags_do_not_hide_explicit_source_cooking_or_invent_estimates() -> None:
    source = dish().model_copy(
        update={"categories": [], "methods": [], "steps": "煮10分钟，供2人食用。"}
    )
    card = build_card(source)
    assert card.subtitle == "煮" and card.badges == ["煮"]
    assert card.cooking_minutes is None and card.servings is None


def test_source_quality_snapshot_counts_finished_method_not_bad_directory_tags() -> None:
    record = dish()
    measured = menu_quality_snapshot([record.recipe_id], {record.recipe_id: record})
    assert measured["method_count"] == 1


def test_false_tags_cannot_claim_method_novelty_against_another_steamed_dish() -> None:
    record = dish()
    other = record.model_copy(update={"recipe_id": "other", "methods": ["蒸"]})
    assert menu_similarity_penalty(record, [other])[2] == 1.0


def test_normalization_keeps_raw_steps_but_not_false_finished_method_tags() -> None:
    steps = "白菜切末拌入肉馅，放入烤肠模具。蒸熟后装盘。"
    normalized = normalize_recipes(
        [
            {
                "名称": "作者蒸菜",
                "食材清单": "鸡肉200克；白菜50克",
                "烹饪步骤": steps,
                "label": "晚餐",
            }
        ]
    )
    record = next(iter(normalized.values()))
    assert record.methods == ["蒸"]
    assert record.steps == steps


def test_unknown_method_coverage_survives_aggregation_without_becoming_novelty() -> None:
    known = dish()
    unknown = known.model_copy(update={"recipe_id": "unknown", "steps": "制作完成。"})
    records = {r.recipe_id: r for r in (known, unknown)}
    measured = menu_quality_snapshot([known.recipe_id, unknown.recipe_id], records)
    assert measured["method_count"] == 1
    assert measured["method_known_dishes"] == measured["method_unknown_dishes"] == 1
    summary = summarize_menu_quality([{"turns": [{"menu_quality": measured}]}])
    assert summary["method_known_dishes"] == summary["method_unknown_dishes"] == 1
    assert summary["method_coverage_menus"] == 1
    assert summary["method_evidence_version"] == "source-finishing-method-v5"
    legacy = {
        "status": "available",
        "temperature_counts": {},
        "role_coverage": 1,
        "method_count": 4,
    }
    old = summarize_menu_quality([{"turns": [{"menu_quality": legacy}]}])
    assert old["method_coverage_menus"] == 0 and old["method_known_dishes"] is None


def test_explicit_blanched_dish_retains_its_source_badge_not_sauce_boiling() -> None:
    source = dish().model_copy(
        update={"steps": "白菜焯水后装盘。另将生抽熬煮，将料汁淋到白菜上食用。"}
    )
    card = build_card(source)
    assert "焯" in card.badges and "煮" not in card.badges


@pytest.mark.parametrize("equipment", ["蒸烤盘", "蒸烤架", "煎锅", "空气炸锅"])
def test_equipment_alone_never_supplies_hot_serving_evidence(equipment: str) -> None:
    source = dish().model_copy(update={"name": "配菜", "steps": f"准备{equipment}，食材切片备用。"})
    assert serving_temperature(source) == "unknown"
