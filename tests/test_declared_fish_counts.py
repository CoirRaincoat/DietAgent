"""Raw whole-fish counts are food declarations, not masses or nutrition."""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.models import Constraints
from app.domain.protein_food_names import declared_protein_foods
from app.domain.protein_food_references import named_protein_foods
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog, dish


@pytest.mark.parametrize("food", ["鳊鱼", "鳊鱼一条", "鲜活鳊鱼一条", "鱼两条", "鸡蛋两个"])
def test_complete_declared_food_and_literal_count_are_recognized_without_grams(food):
    key = "鸡蛋" if food.startswith("鸡蛋") else "鱼"
    assert key in declared_protein_foods(food)


def test_live_original_bream_reference_no_longer_claims_missing_fish():
    source = catalog()[307]
    assert source.name == "家常清蒸鳊鱼"
    assert source.ingredients[0].name == "鳊鱼一条"
    assert source.ingredients[0].quantity is None
    assert "鱼" in named_protein_foods(source)
    constraints = Constraints(dish_count=1, soup_count=0, preferred_ingredients=["鱼"])
    result = MenuPlanner(RuleEngine()).plan([source], constraints, current=[source])
    assert not result.failure and result.recipes == [source]
    assert not any(
        w.startswith("尚未覆盖有实际食材依据的蛋白菜名称参考：") for w in result.warnings
    )


@pytest.mark.parametrize(
    "food",
    [
        "鳊鱼味复合酱一条",
        "鱼露一条",
        "鱼香酱一条",
        "鱼腥草一条",
        "鱿鱼一条",
        "鳊鱼汤一碗",
        "鳊鱼可能一条",
        "鳊鱼一条或虾一只",
    ],
)
def test_count_suffix_never_strips_compounds_or_infers_an_unknown_fish(food):
    assert "鱼" not in declared_protein_foods(food)


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("清蒸鳊鱼", "白菜200克", "白菜蒸熟后装盘。"),
        ("鱼香肉丝", "猪肉200克；鳊鱼一条", "猪肉和鱼蒸熟后装盘。"),
        ("鳊鱼汤", "鳊鱼一条；水300克", "鳊鱼和水煮汤后连汤盛入碗。"),
        ("鳊鱼饭", "鳊鱼一条；大米100克", "米饭煮熟后和鱼蒸熟食用。"),
    ],
)
def test_name_role_and_actual_source_food_still_jointly_required(name, foods, steps):
    assert "鱼" not in named_protein_foods(dish(name, foods, steps))


def test_recognized_fish_never_bypasses_diet_allergy_or_spice():
    source = catalog()[307]
    for changes in (
        {"allergies": ["鱼"]},
        {"allergies": ["海鲜"]},
        {"diet_mode": "vegan"},
        {"diet_mode": "ovo_lacto_vegetarian"},
    ):
        assert (
            MenuPlanner(RuleEngine()).plan([source], Constraints(dish_count=1, **changes)).failure
        )
    spicy = dish("清蒸鳊鱼", "鳊鱼一条；辣椒10克", "鳊鱼和辣椒蒸熟装盘。")
    assert MenuPlanner(RuleEngine()).plan([spicy], Constraints(dish_count=1, no_spicy=True)).failure


def test_local_food_reference_does_not_authorize_replacing_other_slots():
    fish = dish("清蒸鳊鱼", "鳊鱼一条", "鳊鱼蒸熟装盘。")
    egg = dish("蒸鸡蛋", "鸡蛋2个；水100克", "鸡蛋蒸熟食用。")
    greens = dish("蒸白菜", "白菜200克", "白菜蒸熟装盘。")
    rice = dish("米饭", "大米100克；水150克", "米饭煮熟食用。")
    constraints = Constraints(dish_count=3, soup_count=0, preferred_ingredients=["鱼"])
    result = MenuPlanner(RuleEngine()).plan(
        [egg, fish, greens, rice], constraints, current=[egg, greens, rice], replace_slot=2
    )
    assert not result.failure
    assert result.recipes[0] == egg and result.recipes[2] == rice
    # The authorized target may become fish. Only the other two slots are
    # frozen; a food-reference fix must not expand that edit scope.
