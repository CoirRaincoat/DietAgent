"""Development contrasts for an OFF-by-default ranking ablation, not health gold."""

import pytest

from app.agent.initial_goal_priority import source_goal_frontier
from app.agent.planner import MenuPlanner
from app.domain.cooking_methods import main_cooking_methods
from app.domain.models import Constraints
from app.nutrition.structured import analyze_recipe
from app.rules.engine import RuleEngine
from tests.test_component_slots import dish


def vegetables():
    return [
        dish("蒸菠菜", "菠菜200克；水100克", "菠菜蒸熟后装盘。"),
        dish("蒸白菜", "白菜200克；水100克", "白菜蒸熟后装盘。"),
        dish("煮青菜", "青菜200克；水100克", "青菜煮熟后装盘。"),
    ]


def unknown_vegetable():
    return dish("家常西兰花", "西兰花200克", "放入设备按提示烹饪，完成后装盘。")


def test_equal_and_incomparable_goal_vectors_keep_original_order():
    a, b, c = vegetables()
    scores = {a.recipe_id: (2, 0), b.recipe_id: (0, 2), c.recipe_id: (2, 0)}
    assert source_goal_frontier([c, b, a], scores) == [c, b, a]


def test_strict_dominance_works_for_negative_as_well_as_positive_goals():
    a, b, c = vegetables()
    scores = {a.recipe_id: (-2, 0), b.recipe_id: (-1, 0), c.recipe_id: (-1, 1)}
    assert source_goal_frontier([a, b, c], scores) == [c]


def test_higher_proxy_unknown_does_not_eliminate_known_finishing_evidence():
    known, unknown = vegetables()[0], unknown_vegetable()
    assert not main_cooking_methods(unknown)
    assert source_goal_frontier(
        [known, unknown], {known.recipe_id: (0,), unknown.recipe_id: (3,)}
    ) == [known, unknown]


def test_known_finishing_evidence_can_dominate_equal_proxy_unknown():
    known, unknown = vegetables()[0], unknown_vegetable()
    assert source_goal_frontier(
        [unknown, known], {known.recipe_id: (0,), unknown.recipe_id: (0,)}
    ) == [known]


@pytest.mark.parametrize("pool", [[], vegetables()])
def test_unscored_input_is_identity(pool):
    assert source_goal_frontier(pool, {}) == pool


def test_zero_width_vectors_are_identity_not_a_new_knowledge_rank():
    pool = [unknown_vegetable(), *vegetables()]
    assert source_goal_frontier(pool, {r.recipe_id: () for r in pool}) == pool


def test_missing_or_inconsistent_vectors_fail_closed():
    a, b, _ = vegetables()
    with pytest.raises(KeyError):
        source_goal_frontier([a, b], {a.recipe_id: (1,)})
    with pytest.raises(ValueError, match="same width"):
        source_goal_frontier([a, b], {a.recipe_id: (1,), b.recipe_id: (1, 2)})


def initial_menu():
    return [
        dish("蒸白菜", "白菜200克；水100克", "白菜蒸熟后装盘。"),
        dish("煮鸡蛋", "鸡蛋200克；水100克", "鸡蛋煮熟后食用。"),
        dish("烤糯米饭", "糯米200克；水100克", "糯米加水烤熟后食用。"),
    ]


def cold_vs_known_pool():
    return [
        dish("凉拌菠菜", "菠菜200克；盐20克", "菠菜焯熟沥干后放凉，加盐凉拌后装盘。"),
        vegetables()[0],
    ]


def test_salt_presence_alone_does_not_license_a_generic_diversity_tradeoff():
    current, pool = initial_menu(), cold_vs_known_pool()
    constraints = Constraints(
        dish_count=4, meal_type="晚餐", health_goals=["降压", "护心"], preferences=["做法多样"]
    )
    planner = MenuPlanner(RuleEngine())
    baseline = planner.plan(pool, constraints, current=current)
    trial = planner.plan(pool, constraints, current=current, experiment_initial_goal_frontier=True)
    assert baseline.failure is None and trial.failure is None
    # Missing salt cannot be interpreted as a measured sodium improvement.
    assert baseline.recipes == [*current, pool[0]]
    assert trial.recipes == [*current, pool[0]]
    assert baseline.recipes[:3] == trial.recipes[:3]
    assert not any("一般做法多样性取舍" in w for w in baseline.warnings)
    assert any("含钠来源待核" in reason for reason in analyze_recipe(pool[0], constraints).goal_matches[0].reasons)


@pytest.mark.parametrize("goals", [[], ["未知目标"], ["未知目标", "未知目标"]])
def test_unconfigured_goals_do_not_change_default_order(goals):
    current, pool = initial_menu(), cold_vs_known_pool()
    constraints = Constraints(dish_count=4, health_goals=goals, preferences=["做法多样"])
    planner = MenuPlanner(RuleEngine())
    assert (
        planner.plan(pool, constraints, current=current).recipes
        == planner.plan(
            pool, constraints, current=current, experiment_initial_goal_frontier=True
        ).recipes
    )


@pytest.mark.parametrize("unsafe_kind", ["辣椒", "虾", "baby", "drink"])
def test_frontier_never_waives_hard_or_main_meal_gates(unsafe_kind):
    safe = dish("蒸菠菜", "菠菜200克；盐20克", "菠菜加盐蒸熟后装盘。")
    unsafe = (
        dish("宝宝菠菜", "菠菜200克", "菠菜煮熟，宝宝辅食。")
        if unsafe_kind == "baby"
        else (
            dish("黄瓜汁", "黄瓜200克；水100克", "黄瓜榨汁后饮用。")
            if unsafe_kind == "drink"
            else dish("蒸菠菜" + unsafe_kind, f"菠菜200克；{unsafe_kind}50克", "全部食材蒸熟装盘。")
        )
    )
    constraints = Constraints(
        dish_count=1, health_goals=["降压", "护心"], no_spicy=True, allergies=["虾"]
    )
    result = MenuPlanner(RuleEngine()).plan(
        [unsafe, safe], constraints, experiment_initial_goal_frontier=True
    )
    assert result.failure is None and result.recipes == [safe]


def test_confirmed_meal_context_precedes_goal_frontier():
    dinner = dish("蒸菠菜", "菠菜200克；盐20克", "菠菜加盐蒸熟装盘。")
    breakfast = dish("煮白菜", "白菜200克", "白菜煮熟装盘。").model_copy(
        update={"meal_types": ["早餐"], "raw_label": "早餐"}
    )
    constraints = Constraints(dish_count=1, meal_type="晚餐", health_goals=["降压"])
    result = MenuPlanner(RuleEngine()).plan(
        [breakfast, dinner], constraints, experiment_initial_goal_frontier=True
    )
    assert result.recipes == [dinner]


def test_unfilled_culinary_role_precedes_goal_frontier():
    current = initial_menu()[:2]
    rice = dish("蒸糯米饭", "糯米200克；盐20克；水100克", "糯米加水盐蒸熟后食用。")
    constraints = Constraints(dish_count=3, health_goals=["降压", "护心"])
    result = MenuPlanner(RuleEngine()).plan(
        [vegetables()[0], rice], constraints, current=current, experiment_initial_goal_frontier=True
    )
    assert result.recipes == [*current, rice]


def test_authorized_explicit_method_priority_precedes_goal_frontier():
    steam = dish("蒸鸡肉", "鸡肉200克；盐20克", "鸡肉加盐蒸熟后装盘。")
    boil = dish("煮鸡肉", "鸡肉200克；水100克", "鸡肉煮熟后装盘。")
    constraints = Constraints(
        dish_count=1, health_goals=["护心"], preferences=["做法：蒸"], method_meal_priority="method"
    )
    result = MenuPlanner(RuleEngine()).plan(
        [boil, steam], constraints, experiment_initial_goal_frontier=True
    )
    assert result.recipes == [steam]


def test_plain_continue_preserves_menu_and_local_edit_keeps_other_slots():
    current = initial_menu()
    pool = cold_vs_known_pool()
    constraints = Constraints(dish_count=4, health_goals=["降压", "护心"], preferences=["做法多样"])
    planner = MenuPlanner(RuleEngine())
    retained = [*current, pool[0]]
    continuation = planner.plan(
        pool,
        constraints,
        current=retained,
        recheck_soft_preferences=False,
        experiment_initial_goal_frontier=True,
    )
    assert continuation.recipes == retained and not continuation.changes
    local = planner.plan(
        pool, constraints, current=retained, replace_slot=4, experiment_initial_goal_frontier=True
    )
    assert local.recipes == [*current, pool[1]]
    assert local.recipes[:3] == retained[:3]


def test_duplicate_known_goals_and_unknown_goals_cannot_add_priority_dimensions():
    current, pool = initial_menu(), cold_vs_known_pool()
    planner = MenuPlanner(RuleEngine())
    base = Constraints(dish_count=4, health_goals=["护心"], preferences=["做法多样"])
    duplicate = base.model_copy(update={"health_goals": ["未知目标", "护心", "护心"]})
    assert (
        planner.plan(pool, base, current=current, experiment_initial_goal_frontier=True).recipes
        == planner.plan(
            pool, duplicate, current=current, experiment_initial_goal_frontier=True
        ).recipes
    )
