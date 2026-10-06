"""Authored named-dish contrasts; not a nutrition or dominance gold set."""

from typing import Any

import pytest

from app.agent.planner import MenuPlanner
from app.agent.protein_food_preferences import repair_protein_food_preferences
from app.agent.suggestions import replacement_candidates
from app.domain.models import Constraints, Recipe
from app.domain.protein_food_references import named_protein_foods
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine


def dish(
    name: str, foods: str, steps: str = "所有食材蒸熟装盘。", label: str = "午餐、晚餐"
) -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [{"名称": name, "食材清单": foods, "烹饪步骤": steps, "label": label}]
            ).values()
        )
    )


@pytest.mark.parametrize(
    "name,foods,expected",
    [
        ("蒸鸡蛋", "鸡蛋2个；水100克", {"鸡蛋"}),
        ("番茄炒鸡蛋", "番茄100克；鸡蛋2个", {"鸡蛋"}),
        ("鸡肉玉米肠", "鸡肉200克；鸡蛋1个；玉米50克", {"鸡肉"}),
        ("鲫鱼豆腐汤", "鲫鱼200克；豆腐100克；水500克", set()),
        ("清蒸黄鱼", "黄鱼200克；姜5克", {"鱼"}),
        ("鲜虾蒸蛋", "虾仁100克；鸡蛋2个", {"虾", "鸡蛋"}),
        ("虾皮蒸蛋", "虾皮5克；鸡蛋2个", {"鸡蛋"}),
        ("鱼香肉丝", "猪肉200克；鱼露5克", {"猪肉"}),
        ("鱼汤蒸蛋", "鱼汤100克；鸡蛋2个", {"鸡蛋"}),
        ("蟹肉蒸蛋", "蟹肉100克；鸡蛋2个", {"鸡蛋"}),
        ("蛋黄焗鸡肉", "鸡肉200克；蛋黄1个", {"鸡肉"}),
        ("鸡腿菇炖豆腐", "鸡腿菇100克；豆腐200克", {"豆腐"}),
        ("鸭梨蒸蛋", "鸭梨100克；鸡蛋2个", {"鸡蛋"}),
        ("虾仁", "蟹肉200克", set()),
        ("鲈鱼", "白菜200克", set()),
        ("蒸鸡蛋", "鸭蛋2个", set()),
        ("蒸蛋", "鸡蛋2个；水100克", {"鸡蛋"}),
        ("猪肉丸", "肉末200克", set()),
        ("鱼香肉丝", "猪肉200克；鱼肉100克", {"猪肉"}),
        ("蒸鱼", "鱼露20克；鸡蛋1个", set()),
        ("鸡肉蒸蛋", "鸡肉200克；鸡蛋2个", {"鸡肉", "鸡蛋"}),
        ("鸡肉米饭", "大米200克；鸡肉100克；鸡蛋1个", set()),
        ("蒸牛腱肉", "牛腱肉200克", {"牛肉"}),
        ("炖牛腩肉", "牛腩肉200克", {"牛肉"}),
        # Whole declared beef and its matching title now have source evidence.
        ("蒸肥牛", "肥牛200克", {"牛肉"}),
    ],
)
def test_declared_named_reference_is_not_incidental_allergen_family(
    name: str, foods: str, expected: set[str]
) -> None:
    assert named_protein_foods(dish(name, foods)) == expected


def test_ingredient_presence_does_not_stop_egg_dish_preference_repair() -> None:
    sausage = dish("鸡肉玉米肠", "鸡肉200克；鸡蛋1个；玉米50克")
    egg = dish("蒸蛋", "鸡蛋2个；水100克")
    veg = dish("蒸花菜", "花菜200克")
    rice = dish("米饭", "大米200克；水300克", "煮熟食用。")
    constraints = Constraints(preferred_ingredients=["鸡蛋"])
    result = MenuPlanner(RuleEngine()).plan(
        [sausage, veg, rice, egg], constraints, current=[sausage, veg, rice]
    )
    assert egg.recipe_id in {r.recipe_id for r in result.recipes}
    assert [r.recipe_id for r in result.recipes[1:]] == [veg.recipe_id, rice.recipe_id]


def test_fish_soup_does_not_stop_named_protein_dish_reference_repair() -> None:
    pork = dish("蒸猪肉", "猪肉200克")
    fish = dish("清蒸黄鱼", "黄鱼200克")
    soup = dish("鲫鱼豆腐汤", "鲫鱼200克；豆腐100克；水500克")
    constraints = Constraints(dish_count=2, soup_count=1, preferred_ingredients=["鱼"])
    result = MenuPlanner(RuleEngine()).plan([pork, soup, fish], constraints, current=[pork, soup])
    assert [r.recipe_id for r in result.recipes] == [fish.recipe_id, soup.recipe_id]


def pool() -> tuple[list[Recipe], Recipe]:
    sausage = dish("鸡肉玉米肠", "鸡肉200克；鸡蛋1个；玉米50克")
    veg = dish("蒸花菜", "花菜200克")
    rice = dish("米饭", "大米200克；水300克", "煮熟食用。")
    egg = dish("蒸鸡蛋", "鸡蛋2个；水100克")
    return [sausage, veg, rice], egg


@pytest.mark.parametrize("local,recheck", [(2, True), (None, False)])
def test_unrelated_local_edit_or_continue_never_grants_protein_slot_permission(
    local: int | None, recheck: bool
) -> None:
    original, egg = pool()
    result = MenuPlanner(RuleEngine()).plan(
        [*original, egg, dish("蒸白菜", "白菜200克")],
        Constraints(preferred_ingredients=["鸡蛋"]),
        current=original,
        replace_slot=local,
        recheck_soft_preferences=recheck,
    )
    assert result.recipes[0] == original[0]
    assert any("蛋白菜名称参考：鸡蛋" in warning for warning in result.warnings)


@pytest.mark.parametrize("restriction", [{"allergies": ["鸡蛋"]}, {"no_spicy": True}])
def test_named_preference_never_bypasses_allergy_or_spice(restriction: dict[str, Any]) -> None:
    original, _ = pool()
    # Remove the egg binder from the old meat dish so the old menu is legal.
    original[0] = dish("蒸鸡肉", "鸡肉200克")
    unsafe = dish("辣蒸鸡蛋", "鸡蛋2个；辣椒5克")
    result = MenuPlanner(RuleEngine()).plan(
        [*original, unsafe],
        Constraints(preferred_ingredients=["鸡蛋"], **restriction),
        current=original,
    )
    assert unsafe.recipe_id not in {r.recipe_id for r in result.recipes}
    assert any("蛋白菜名称参考：鸡蛋" in warning for warning in result.warnings)


def test_suggestions_do_not_replace_unique_egg_named_reference_with_binder_match() -> None:
    original, egg = pool()
    alternative = dish("另蒸鸡蛋", "鸡蛋2个；水100克")
    pork_binder = dish("蒸猪肉", "猪肉200克；鸡蛋1个")
    menu = [egg, original[0], original[2]]
    result = replacement_candidates(
        menu,
        [*menu, alternative, pork_binder],
        "named-egg",
        constraints=Constraints(preferred_ingredients=["鸡蛋"]),
        rules=RuleEngine(),
    )
    assert alternative in result
    assert pork_binder not in result


@pytest.mark.parametrize(
    "blocked",
    [
        "health",
        "meal",
        "flavor",
        "scene",
        "method",
        "composition",
        "missing_score",
        "named_reference",
    ],
)
def test_direct_repair_protects_each_preexisting_fact(blocked: str) -> None:
    original, egg = pool()
    original = original[:1]
    constraints = Constraints(dish_count=1, preferred_ingredients=["鸡蛋"])
    old = original[0]
    scores: dict[str, tuple[int, ...]] = {old.recipe_id: (), egg.recipe_id: ()}
    if blocked == "health":
        constraints.health_goals = ["护心"]
        scores = {old.recipe_id: (2,), egg.recipe_id: (1,)}
    elif blocked == "meal":
        old = old.model_copy(update={"raw_label": "晚餐", "meal_types": ["晚餐"]})
        egg = egg.model_copy(update={"raw_label": "早餐", "meal_types": ["早餐"]})
    elif blocked == "flavor":
        old = old.model_copy(update={"raw_label": "晚餐、清淡"})
        constraints.preferences = ["清淡"]
    elif blocked == "scene":
        old = old.model_copy(update={"raw_label": "晚餐、家常"})
        constraints.preferences = ["家常"]
    elif blocked == "method":
        old = dish("煮鸡肉玉米肠", "鸡肉200克；鸡蛋1个；玉米50克", "所有食材煮熟装盘。")
        constraints.preferences = ["做法：煮"]
    elif blocked == "composition":
        constraints.meat_dish_count = 1
        constraints.meat_dish_scope = "independent_entree"
        old = dish("蒸鸡肉", "鸡肉200克；鸡蛋1个")
    elif blocked == "missing_score":
        scores = {old.recipe_id: ()}
    elif blocked == "named_reference":
        constraints.preferred_ingredients = ["鸡肉", "鸡蛋"]
    # Re-key vectors when the fixture source is intentionally changed.
    if blocked not in ("health", "missing_score"):
        scores = {old.recipe_id: (), egg.recipe_id: ()}
    rules = RuleEngine()
    result = repair_protein_food_preferences(
        [old],
        [old, egg],
        constraints,
        canonical_food=rules.canonical_food,
        food_matches=lambda r, t: bool(rules.food_matches(r, t)),
        goal_scores=scores,
        order={old.recipe_id: 0, egg.recipe_id: 1},
    )
    assert result.recipes == [old]


def test_two_missing_named_foods_can_be_covered_by_one_mixed_dish_without_new_slots() -> None:
    old = dish("蒸猪肉", "猪肉200克；鸡蛋1个；虾仁50克")
    shrimp_egg = dish("鲜虾蒸蛋", "鸡蛋2个；虾仁100克")
    c = Constraints(dish_count=1, preferred_ingredients=["鸡蛋", "虾"])
    result = MenuPlanner(RuleEngine()).plan([old, shrimp_egg], c, current=[old])
    assert result.recipes == [shrimp_egg]
    repeat = MenuPlanner(RuleEngine()).plan([old, shrimp_egg], c, current=result.recipes)
    assert repeat.recipes == result.recipes and not repeat.changes


def test_named_repair_precedes_raw_coverage_to_avoid_changing_two_slots() -> None:
    old = dish("蒸鸡肉", "鸡肉200克")
    veg = dish("蒸花菜", "花菜200克")
    rice = dish("米饭", "大米200克；水300克", "煮熟食用。")
    beef_rice = dish("牛肉米饭", "大米200克；牛肉100克", "煮熟食用。")
    beef = dish("蒸牛腱肉", "牛腱肉200克")
    original = [old, veg, rice]
    result = MenuPlanner(RuleEngine()).plan(
        [beef_rice, beef, *original],
        Constraints(preferred_ingredients=["牛肉"]),
        current=original,
    )
    assert result.recipes == [beef, veg, rice]
    assert len(result.changes) == 1
