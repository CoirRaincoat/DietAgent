import pytest
from pydantic import ValidationError

from app.api.presentation import build_card, recipe_provenance, split_cooking_steps
from app.domain.cards import CookingStep, DishCard, RecipeProvenance
from app.domain.models import Ingredient, Recipe


def sample_recipe(**updates):
    values = {
        "recipe_id": "source-recipe", "name": "西兰花鸡蛋", "source_row": 8,
        "fingerprint": "a" * 64, "raw_ingredients": "西兰花；鸡蛋",
        "ingredients": [Ingredient(name="西兰花", raw="西兰花"), Ingredient(name="鸡蛋", raw="鸡蛋")],
        "steps": "先洗菜。\n再煮熟。", "categories": ["vegetable", "protein"],
        "methods": ["煮"], "labels": ["清淡", "晚餐"],
    }
    values.update(updates)
    return Recipe(**values)


def test_card_uses_real_title_and_whitelisted_metadata():
    source = sample_recipe()
    card = build_card(source)
    assert card.title == source.name
    assert card.subtitle == "蔬菜类 · 煮"
    assert card.badges == ["蔬菜类", "煮", "清淡", "晚餐"]
    assert card.image_url is None and card.cooking_minutes is None and card.servings is None
    assert DishCard.model_validate_json(card.model_dump_json()) == card


def test_badges_cannot_promote_unknown_or_health_claims():
    source = sample_recipe(
        categories=["protein", "低糖", "vegetable", "vegetable"],
        methods=["降糖烹饪", "煮", "煮"],
        labels=["低钠", "高蛋白", "抗癌", "降血糖", "清淡", "清淡"],
        raw_label="低钠高蛋白抗癌降血糖",
    )
    assert build_card(source).badges == ["蔬菜类", "煮", "清淡"]
    assert "低钠" not in build_card(source).model_dump_json()


def test_missing_metadata_has_neutral_subtitle_and_null_estimates():
    source = sample_recipe(categories=[], methods=[], labels=[], steps="煮10分钟，供2人食用。")
    card = build_card(source)
    assert card.subtitle == "查看食材与做法"
    assert card.badges == []
    assert card.cooking_minutes is None and card.servings is None


def test_source_numbers_warnings_and_punctuation_are_preserved():
    original = "  1. 洗菜。\r\n\r\n2）放入锅中；切勿用手触碰热锅！  \r备注：可选配料必须先核对过敏。\n"
    result = split_cooking_steps(original)
    assert [step.number for step in result] == [1, 2, 3]
    assert [step.description for step in result] == [
        "  1. 洗菜。", "2）放入锅中；切勿用手触碰热锅！  ", "备注：可选配料必须先核对过敏。",
    ]
    # No sentences, semicolons or original numbering are heuristically removed.
    assert "".join(step.description for step in result) == original.replace("\r", "").replace("\n", "")


def test_single_paragraph_is_not_rewritten_into_invented_steps():
    paragraph = "1、洗菜；2、煮熟。冷却后食用。"
    assert split_cooking_steps(paragraph) == [CookingStep(number=1, description=paragraph)]
    assert split_cooking_steps("\r\n  \n\t") == []


def test_provenance_is_exact_source_identity():
    source = sample_recipe()
    provenance = recipe_provenance(source)
    assert provenance.model_dump() == {
        "recipe_id": source.recipe_id, "source_row": source.source_row, "fingerprint": source.fingerprint,
    }
    assert RecipeProvenance.model_validate_json(provenance.model_dump_json()) == provenance


@pytest.mark.parametrize("field,value", [("image_url", "https://example.test/guessed.png"),
                                         ("cooking_minutes", 10), ("servings", 2)])
def test_unknown_data_cannot_be_filled_with_estimates(field, value):
    with pytest.raises(ValidationError):
        DishCard(title="真实菜名", subtitle="", **{field: value})


def test_contracts_reject_extra_keys_and_invalid_step_numbers():
    with pytest.raises(ValidationError):
        DishCard(title="真实菜名", subtitle="", calories=500)
    with pytest.raises(ValidationError):
        CookingStep(number=0, description="真实步骤")
    with pytest.raises(ValidationError):
        RecipeProvenance(recipe_id="a", source_row=1, fingerprint="b", invented=True)