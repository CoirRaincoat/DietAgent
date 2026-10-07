"""Authored family-repeat fixtures; not clinical, source or human quality scores."""

from collections.abc import Sequence

import pytest

from app.agent.menu_variety import repair_menu_variety
from app.agent.planner import MenuPlanner
from app.domain.food_variety import food_families
from app.domain.models import Constraints, Ingredient, Recipe
from app.rules.engine import RuleEngine


def dish(key: str, foods: Sequence[str], role: str = "vegetable") -> Recipe:
    """Make a declared ordinary-meal fixture without private data or API calls."""
    return Recipe(
        recipe_id=key,
        name=key,
        raw_ingredients="、".join(foods),
        steps="食材放入蒸锅，蒸熟后装盘。",
        ingredients=[Ingredient(name=food, raw=food) for food in foods],
        categories=[role],
        meal_types=["晚餐"],
        methods=["蒸"],
        source_row=1,
        fingerprint=key,
    )


@pytest.mark.parametrize("food", ["南瓜", "贝贝南瓜", "熟南瓜块", "老南瓜", "南瓜泥"])
def test_declared_pumpkin_aliases_share_one_family(food: str) -> None:
    assert food_families(dish("配方", [food])) == frozenset({"pumpkin"})


@pytest.mark.parametrize("food", ["南瓜籽油", "南瓜酱", "盐", "水", "未知菜料", "鸡粉"])
def test_condiments_unknowns_and_derived_products_do_not_claim_family(food: str) -> None:
    assert food_families(dish("南瓜鸡肉", [food])) == frozenset()


@pytest.mark.parametrize("food", ["猪肘子酱", "三黄鸡汤粉", "内酯豆腐调味酱", "菜山药酱"])
def test_whole_food_aliases_do_not_accept_derived_seasonings(food: str) -> None:
    assert food_families(dish("配方", [food])) == frozenset()


def test_changed_source_invalidates_family_evidence() -> None:
    record = dish("same-id", ["南瓜"])
    assert food_families(record) == frozenset({"pumpkin"})
    record.ingredients = [Ingredient(name="西兰花", raw="西兰花")]
    assert food_families(record) == frozenset({"broccoli"})


def repair(
    menu: list[Recipe],
    pool: list[Recipe],
    *,
    constraints: Constraints | None = None,
    goals: dict[str, tuple[int, ...]] | None = None,
    scores: dict[str, int] | None = None,
    relevance: dict[str, float] | None = None,
    local: int | None = None,
) -> list[Recipe]:
    """Call the public repair with explicit peer suitability, never network mocks."""
    return repair_menu_variety(
        menu,
        pool,
        constraints or Constraints(dish_count=max(1, len(menu))),
        goal_scores=goals if goals is not None else {r.recipe_id: () for r in pool},
        rule_scores=scores if scores is not None else {r.recipe_id: 0 for r in pool},
        relevance=relevance if relevance is not None else {r.recipe_id: 0.0 for r in pool},
        order={r.recipe_id: i for i, r in enumerate(pool)},
        food_matches=lambda r, term: any(term == i.name for i in r.ingredients),
        replace_slot=local,
    ).recipes


def test_equal_fit_replaces_one_repeated_family_and_reaches_fixed_point() -> None:
    first = dish("甲", ["贝贝南瓜"])
    second = dish("乙", ["南瓜"])
    other = dish("丙", ["西兰花"])
    pool = [first, second, other]
    result = repair([first, second], pool)
    assert result == [other, second]
    assert repair(result, pool) == result


def test_no_repeat_no_swap_and_no_missing_metadata_reward() -> None:
    first = dish("甲", ["南瓜"])
    other = dish("丙", ["西兰花"])
    unknown = dish("未知", [])
    assert repair([first, other], [first, other, unknown]) == [first, other]
    assert repair([first, dish("乙", ["贝贝南瓜"])], [first, unknown]) == [
        first,
        dish("乙", ["贝贝南瓜"]),
    ]


@pytest.mark.parametrize(
    "blocked", ["goal", "rule", "query", "role", "context", "preference", "quota"]
)
def test_variety_cannot_override_suitability_or_scope(blocked: str) -> None:
    first, second, other = dish("甲", ["南瓜"]), dish("乙", ["贝贝南瓜"]), dish("丙", ["西兰花"])
    pool = [first, second, other]
    constraints = Constraints(dish_count=2)
    goals: dict[str, tuple[int, ...]] = {"甲": (2, 0), "乙": (2, 0), "丙": (2, 0)}
    scores = {"甲": 2, "乙": 2, "丙": 2}
    relevance = {"甲": 1.0, "乙": 1.0, "丙": 1.0}
    if blocked == "goal":
        goals["丙"] = (1, 3)  # Larger sum must not hide one goal's regression.
    elif blocked == "rule":
        scores["丙"] = 1
    elif blocked == "query":
        relevance["丙"] = 0.0
    elif blocked == "role":
        other.categories = ["protein"]
    elif blocked == "context":
        other.meal_types = ["早餐"]
    elif blocked == "preference":
        constraints.preferred_ingredients = ["南瓜"]
        # Only the addressed slot covers this exact preferred food.
    elif blocked == "quota":
        other.ingredients.append(Ingredient(name="猪肉", raw="猪肉"))
        constraints.meat_dish_count = 0
        constraints.vegetarian_dish_count = 2
    assert repair(
        [first, second],
        pool,
        constraints=constraints,
        goals=goals,
        scores=scores,
        relevance=relevance,
        local=1,
    ) == [first, second]


def test_multiple_swaps_reduce_whole_menu_not_only_a_single_pair() -> None:
    menu = [dish("甲", ["南瓜"]), dish("乙", ["贝贝南瓜"]), dish("丁", ["南瓜泥"])]
    pool = [*menu, dish("丙", ["西兰花"]), dish("戊", ["冬瓜"])]
    result = repair(menu, pool)
    assert result == [pool[3], pool[4], menu[2]]
    assert repair(result, pool) == result


def test_requested_local_slot_is_the_only_mutable_slot() -> None:
    menu = [dish("甲", ["南瓜"]), dish("乙", ["贝贝南瓜"])]
    other = dish("丙", ["西兰花"])
    assert repair(menu, [*menu, other], local=2) == [menu[0], other]


def test_missing_goal_or_rule_or_query_observation_cannot_authorize_swap() -> None:
    menu = [dish("甲", ["南瓜"]), dish("乙", ["贝贝南瓜"])]
    pool = [*menu, dish("丙", ["西兰花"])]
    assert repair(menu, pool, goals={}) == menu
    assert repair(menu, pool, scores={}) == menu
    assert repair(menu, pool, relevance={}) == menu


def test_empty_single_and_invalid_local_boundaries() -> None:
    one = dish("甲", ["南瓜"])
    assert repair([], []) == []
    assert repair([one], [one]) == [one]
    with pytest.raises(ValueError, match="existing"):
        repair([one, dish("乙", ["贝贝南瓜"])], [one], local=3)


def test_planner_repairs_fresh_peer_variety_but_plain_continue_is_stable() -> None:
    menu = [dish("甲", ["贝贝南瓜"]), dish("乙", ["南瓜"])]
    other = dish("丙", ["西兰花"])
    planner = MenuPlanner(RuleEngine())
    constraints = Constraints(dish_count=2)
    unchanged = planner.plan(
        [*menu, other], constraints, current=menu, recheck_soft_preferences=False
    )
    assert unchanged.recipes == menu
    changed = planner.plan([*menu, other], constraints, current=menu, experiment_menu_variety=True)
    assert [r.recipe_id for r in changed.recipes] == ["丙", "乙"]
    assert "食材重复" in changed.changes[0]["reason"]
    assert planner.plan([*menu, other], constraints, current=menu).recipes == menu
    assert (
        planner.plan(
            [*menu, other],
            constraints,
            current=menu,
            recheck_soft_preferences=False,
            experiment_menu_variety=True,
        ).recipes
        == menu
    )


def test_planner_never_introduces_spicy_or_allergenic_novelty() -> None:
    menu = [dish("甲", ["南瓜"]), dish("乙", ["贝贝南瓜"])]
    forbidden = [dish("辣菜", ["西兰花", "辣椒"]), dish("虾菜", ["冬瓜", "虾"])]
    result = MenuPlanner(RuleEngine()).plan(
        [*menu, *forbidden],
        Constraints(dish_count=2, no_spicy=True, allergies=["虾"]),
        current=menu,
        experiment_menu_variety=True,
    )
    assert result.recipes == menu
