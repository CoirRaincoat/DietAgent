"""Authored peer-fit counterexamples, not independent quality or source labels."""

from collections import Counter
from itertools import product
from typing import Literal

import pytest

from app.agent.menu_variety import repair_menu_variety
from app.agent.planner import MenuPlanner
from app.domain.models import Constraints, Ingredient, Recipe
from app.rules.engine import RuleEngine


def dish(key: str, food: str, method: str = "蒸", role: str = "vegetable") -> Recipe:
    return Recipe(
        recipe_id=key,
        name=food + key,
        raw_ingredients=food,
        ingredients=[Ingredient(name=food, raw=food)],
        steps=f"食材{method}熟后装盘。" if method else "食材放入智能设备，启动机器后装盘。",
        categories=[role],
        meal_types=["晚餐"],
        methods=[method] if method else [],
        source_row=1,
        fingerprint=key,
    )


def run(
    menu: list[Recipe],
    pool: list[Recipe],
    methods: dict[str, frozenset[str]],
    *,
    local: int | None = None,
    declared: dict[str, frozenset[str]] | None = None,
    blocked: str = "",
) -> list[Recipe]:
    c = Constraints(dish_count=len(menu), meal_type="晚餐")
    goals = {r.recipe_id: (2, 1) for r in pool}
    rules = {r.recipe_id: 2 for r in pool}
    relevance = {r.recipe_id: 1.0 for r in pool}
    candidate = pool[-1]
    if blocked == "goal":
        goals[candidate.recipe_id] = (1, 5)
    elif blocked == "rule":
        rules[candidate.recipe_id] = 1
    elif blocked == "query":
        relevance[candidate.recipe_id] = 0.0
    elif blocked == "role":
        candidate.categories = ["protein"]
    elif blocked == "context":
        candidate.meal_types = ["早餐"]
    elif blocked == "preference":
        c.preferred_ingredients = ["南瓜"]
    elif blocked == "quota":
        c.meat_dish_count = 0
        c.vegetarian_dish_count = len(menu)
        candidate.ingredients.append(Ingredient(name="猪肉", raw="猪肉"))
    return repair_menu_variety(
        menu,
        pool,
        c,
        goal_scores=goals,
        rule_scores=rules,
        relevance=relevance,
        order={r.recipe_id: i for i, r in enumerate(pool)},
        food_matches=lambda r, term: any(i.name == term for i in r.ingredients),
        replace_slot=local,
        method_evidence=methods,
        declared_family_evidence=declared,
    ).recipes


def profile(menu: list[Recipe], methods: dict[str, frozenset[str]]) -> tuple[int, int, int, int]:
    # Independently count supplied labels; no production profile helper imported.
    counts = Counter(m for r in menu for m in methods.get(r.recipe_id, ()))
    return (
        sum(bool(methods.get(r.recipe_id)) for r in menu),
        len(counts),
        max(counts.values(), default=0),
        sum(n * (n - 1) // 2 for n in counts.values()),
    )


def test_removing_pumpkin_cannot_replace_only_boiled_dish_with_more_steam() -> None:
    menu = [dish("甲", "南瓜"), dish("乙", "贝贝南瓜", "煮")]
    candidate = dish("丙", "西兰花")
    methods = {"甲": frozenset({"蒸"}), "乙": frozenset({"煮"}), "丙": frozenset({"蒸"})}
    assert run(menu, [*menu, candidate], methods, local=2) == menu


def test_same_distinct_methods_cannot_hide_more_dominant_steam() -> None:
    menu = [
        dish("甲", "南瓜"),
        dish("乙", "贝贝南瓜", "煮"),
        dish("丁", "冬瓜"),
        dish("戊", "白菜", "煮"),
    ]
    candidate = dish("丙", "西兰花")
    methods = {r.recipe_id: frozenset(r.methods) for r in [*menu, candidate]}
    assert run(menu, [*menu, candidate], methods, local=2) == menu


def test_pair_concentration_cannot_increase_under_same_peak_and_coverage() -> None:
    labels = ["蒸", "煮", "蒸", "蒸", "煮", "炒", "炒", "烤"]
    menu = [dish(str(i), "南瓜" if i < 2 else f"未知菜{i}", m) for i, m in enumerate(labels)]
    candidate = dish("new", "西兰花", "炒")
    methods = {r.recipe_id: frozenset(r.methods) for r in [*menu, candidate]}
    assert run(menu, [*menu, candidate], methods, local=2) == menu


def test_equal_peak_and_pairs_cannot_shift_repetition_from_boiling_to_steam() -> None:
    labels = ["蒸", "蒸", "煮", "煮", "煮"]
    menu = [dish(str(i), "南瓜" if i in (0, 2) else f"未知菜{i}", m) for i, m in enumerate(labels)]
    candidate = dish("new", "西兰花", "蒸")
    methods = {r.recipe_id: frozenset(r.methods) for r in [*menu, candidate]}
    # 2蒸+3煮 -> 3蒸+2煮 leaves peak=3, pairs=4, known=5, distinct=2.
    assert run(menu, [*menu, candidate], methods, local=3) == menu


@pytest.mark.parametrize("missing", [True, False])
def test_unknown_method_is_not_a_free_novelty_credit(missing: bool) -> None:
    menu = [dish("甲", "南瓜"), dish("乙", "贝贝南瓜")]
    candidate = dish("丙", "西兰花", "")
    methods = {"甲": frozenset({"蒸"}), "乙": frozenset({"蒸"})}
    if not missing:
        methods["丙"] = frozenset()
    assert run(menu, [*menu, candidate], methods) == menu


def test_new_known_method_can_replace_unknown_without_concentrating_existing_method() -> None:
    menu = [dish("甲", "南瓜"), dish("乙", "贝贝南瓜", "")]
    candidate = dish("丙", "西兰花", "煮")
    methods = {"甲": frozenset({"蒸"}), "乙": frozenset(), "丙": frozenset({"煮"})}
    assert run(menu, [*menu, candidate], methods, local=2) == [menu[0], candidate]


def test_missing_method_evidence_on_both_peers_does_not_claim_coverage_gain() -> None:
    menu = [dish("甲", "南瓜", ""), dish("乙", "贝贝南瓜", "")]
    candidate = dish("丙", "西兰花", "")
    result = run(menu, [*menu, candidate], {})
    assert result == [candidate, menu[1]]
    assert profile(result, {}) == (0, 0, 0, 0)


def test_named_body_gain_cannot_increase_actual_declared_family_pairs() -> None:
    menu = [
        dish("甲", "南瓜"),
        dish("乙", "贝贝南瓜"),
        dish("丁", "大米"),
        dish("戊", "大米"),
        dish("己", "大米"),
    ]
    candidate = dish("丙", "西兰花")
    declared = {
        r.recipe_id: frozenset({"pumpkin" if i < 2 else "rice"}) for i, r in enumerate(menu)
    }
    declared["丙"] = frozenset({"broccoli", "rice"})
    methods = {r.recipe_id: frozenset({"蒸"}) for r in [*menu, candidate]}
    assert run(menu, [*menu, candidate], methods, declared=declared, local=2) == menu


def test_lower_total_declared_pairs_cannot_hide_a_larger_dominant_family() -> None:
    menu = [dish(str(i), "南瓜" if i < 2 else f"未知菜{i}") for i in range(7)]
    declared = {
        "0": frozenset({"rice", "tofu"}),
        "1": frozenset({"rice", "tofu"}),
        "2": frozenset({"pumpkin"}),
        "3": frozenset({"pumpkin"}),
        "4": frozenset({"pumpkin"}),
        "5": frozenset(),
        "6": frozenset(),
        "new": frozenset({"pumpkin"}),
    }
    candidate = dish("new", "西兰花")
    methods = {r.recipe_id: frozenset({"蒸"}) for r in [*menu, candidate]}
    assert run(menu, [*menu, candidate], methods, declared=declared, local=2) == menu


@pytest.mark.parametrize("missing_key", ["甲", "丙"])
@pytest.mark.parametrize("present_empty", [True, False])
def test_missing_declared_source_cannot_hide_an_actual_family_regression(
    missing_key: str, present_empty: bool
) -> None:
    menu = [dish("甲", "南瓜"), dish("乙", "贝贝南瓜")]
    candidate = dish("丙", "西兰花")
    declared = {
        "甲": frozenset({"pumpkin"}),
        "乙": frozenset({"pumpkin"}),
        "丙": frozenset({"broccoli"}),
    }
    if present_empty:
        declared[missing_key] = frozenset()
    else:
        del declared[missing_key]
    methods = {r.recipe_id: frozenset({"蒸"}) for r in [*menu, candidate]}
    assert run(menu, [*menu, candidate], methods, declared=declared, local=1) == menu


@pytest.mark.parametrize(
    "blocked", ["goal", "rule", "query", "role", "context", "preference", "quota"]
)
def test_guards_do_not_relax_existing_peer_fit_boundaries(blocked: str) -> None:
    menu = [dish("甲", "贝贝南瓜"), dish("乙", "南瓜")]
    candidate = dish("丙", "西兰花", "煮")
    methods = {r.recipe_id: frozenset(r.methods) for r in [*menu, candidate]}
    assert run(menu, [*menu, candidate], methods, local=2, blocked=blocked) == menu


@pytest.mark.parametrize("local", [None, 1, 2])
@pytest.mark.parametrize("labels", list(product(["蒸", "煮", ""], repeat=3)))
def test_finite_small_pool_fixed_point_and_componentwise_nonregression(
    labels: tuple[str, ...], local: int | None
) -> None:
    menu = [dish("甲", "南瓜", labels[0]), dish("乙", "贝贝南瓜", labels[1])]
    candidate = dish("丙", "西兰花", labels[2])
    pool = [*menu, candidate]
    methods = {r.recipe_id: frozenset(r.methods) for r in pool}
    result = run(menu, pool, methods, local=local)
    before, after = profile(menu, methods), profile(result, methods)
    assert after[0] >= before[0] and after[1] >= before[1]
    assert after[2] <= before[2] and after[3] <= before[3]
    old_counts = Counter(m for r in menu for m in methods[r.recipe_id])
    new_counts = Counter(m for r in result for m in methods[r.recipe_id])
    assert all(n <= max(1, old_counts[m]) for m, n in new_counts.items())
    assert len({r.recipe_id for r in result}) == 2
    assert run(result, pool, methods, local=local) == result
    if local:
        assert result[2 - local] == menu[2 - local]


@pytest.mark.parametrize("mode", [False, True, "culinary_focus", "culinary_focus_guarded"])
def test_planner_mode_does_not_churn_continuation_or_introduce_unsafe_novelty(
    mode: bool | Literal["culinary_focus", "culinary_focus_guarded"],
) -> None:
    menu = [dish("甲", "南瓜"), dish("乙", "贝贝南瓜")]
    candidates = [dish("丙", "西兰花", "煮"), dish("辣", "白菜", "煮"), dish("虾", "冬瓜", "煮")]
    candidates[1].ingredients.append(Ingredient(name="辣椒", raw="辣椒"))
    candidates[2].ingredients.append(Ingredient(name="虾", raw="虾"))
    planner = MenuPlanner(RuleEngine())
    c = Constraints(dish_count=2, no_spicy=True, allergies=["虾"])
    result = planner.plan([*menu, *candidates], c, current=menu, experiment_menu_variety=mode)
    assert not result.failure
    assert not {"辣", "虾"} & {r.recipe_id for r in result.recipes}
    assert (
        planner.plan(
            [*menu, *candidates],
            c,
            current=result.recipes,
            experiment_menu_variety=mode,
            recheck_soft_preferences=False,
        ).recipes
        == result.recipes
    )


def test_guarded_planner_uses_source_actions_not_legacy_method_tags() -> None:
    menu = [dish("甲", "南瓜"), dish("乙", "贝贝南瓜", "煮")]
    candidate = dish("丙", "西兰花")
    candidate.methods = ["煮"]  # Stale tag must not authorize replacing the only boiling action.
    planner = MenuPlanner(RuleEngine())
    c = Constraints(dish_count=2)
    assert planner.plan(
        [*menu, candidate],
        c,
        current=menu,
        experiment_menu_variety="culinary_focus_guarded",
    ).recipes == [candidate, menu[1]]
    assert planner.plan([*menu, candidate], c, current=menu).recipes == menu
