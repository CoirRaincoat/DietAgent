"""Independent recipe-text contrasts for bounded flavor repair invariants."""

from collections import Counter

import pytest

from app.agent.flavor_preferences import repair_flavor_preferences
from app.domain.dish_composition import dish_kind
from app.domain.matching_tags import flavor_coverage, supported_flavor_preferences
from app.domain.models import Constraints
from app.infrastructure.data import normalize_recipes


def recipe(name, foods="白菜；盐；水", steps="白菜加水蒸熟装盘。", label="晚餐"):
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": foods,
                        "烹饪步骤": steps,
                        "label": label,
                    }
                ]
            ).values()
        )
    )


def repair(menu, candidates, constraints=None, **updates):
    values = {
        "goal_scores": {record.recipe_id: () for record in [*menu, *candidates]},
        "order": {record.recipe_id: i for i, record in enumerate(candidates)},
        "food_matches": lambda record, term: any(
            term == ingredient.name for ingredient in record.ingredients
        ),
    }
    values.update(updates)
    return repair_flavor_preferences(
        menu, candidates, constraints or Constraints(preferences=["酸"]), **values
    )


def test_added_acid_can_adjust_source_meat_vegetable_to_no_meat_vegetable_when_no_quota():
    previous = recipe("奶汁白菜", foods="白菜；猪肉；牛奶；盐")
    sour = recipe("醋溜白菜", foods="白菜；醋；盐", label="晚餐、酸")
    assert previous.categories == sour.categories == ["vegetable"]
    assert dish_kind(previous) == "meat" and dish_kind(sour) == "vegetarian"
    before = previous.model_dump_json()
    result = repair([previous], [previous, sour])
    assert result.recipes == [sour] and result.changed_positions == frozenset({0})
    assert previous.model_dump_json() == before
    assert any("不是实际尝味" in warning for warning in result.warnings)


def test_explicit_meat_quota_blocks_otherwise_compatible_sour_vegetable_swap():
    previous = recipe("奶汁白菜", foods="白菜；猪肉；牛奶；盐")
    sour = recipe("醋溜白菜", foods="白菜；醋；盐", label="晚餐、酸")
    constraints = Constraints(preferences=["酸"], dish_count=1, meat_dish_count=1)
    result = repair([previous], [previous, sour], constraints)
    assert result.recipes == [previous] and not result.changed_positions
    assert any("未覆盖" in warning and "酸" in warning for warning in result.warnings)


def test_existing_flavor_coverage_is_never_traded_away_for_a_new_flavor():
    previous = recipe("蒜香白菜")
    sour = recipe("酸味白菜", label="晚餐、酸")
    both = recipe("双味白菜", label="晚餐、酸、蒜香")
    constraints = Constraints(preferences=["蒜香", "酸"])
    blocked = repair([previous], [previous, sour], constraints)
    assert blocked.recipes == [previous]
    improved = repair([previous], [previous, sour, both], constraints)
    assert improved.recipes == [both]
    assert flavor_coverage(both, constraints.preferences) == 0b11


def test_each_goal_is_preserved_not_just_aggregate_gain():
    previous = recipe("原白菜")
    tradeoff = recipe("酸味白菜", label="晚餐、酸")
    nonregressing = recipe("双目标白菜", label="晚餐、酸")
    scores = {
        previous.recipe_id: (1, 1),
        tradeoff.recipe_id: (0, 10),
        nonregressing.recipe_id: (1, 2),
    }
    result = repair(
        [previous],
        [previous, tradeoff, nonregressing],
        Constraints(preferences=["酸"], health_goals=["降压", "护心"]),
        goal_scores=scores,
    )
    assert result.recipes == [nonregressing]


@pytest.mark.parametrize("missing", ["old", "new", "all", "dimension"])
def test_missing_or_incompatible_goal_vectors_never_authorize_a_swap(missing):
    previous = recipe("原白菜")
    sour = recipe("酸味白菜", label="晚餐、酸")
    scores = {previous.recipe_id: (), sour.recipe_id: ()}
    if missing == "old":
        scores.pop(previous.recipe_id)
    elif missing == "new":
        scores.pop(sour.recipe_id)
    elif missing == "all":
        scores.clear()
    else:
        scores[sour.recipe_id] = (1,)
    result = repair([previous], [previous, sour], goal_scores=scores)
    assert result.recipes == [previous] and not result.changed_positions


def test_explicit_empty_vectors_cannot_erase_requested_health_goal_protection():
    previous = recipe("原白菜")
    sour = recipe("酸味白菜", label="晚餐、酸")
    constraints = Constraints(preferences=["酸"], health_goals=["护心"])
    result = repair([previous], [previous, sour], constraints)
    assert result.recipes == [previous] and not result.changed_positions


def test_covered_ingredient_preference_cannot_be_lost_to_sour_flavor():
    previous = recipe("原菌菇白菜", foods="白菜；香菇；盐")
    sour = recipe("醋溜白菜", label="晚餐、酸")
    covered = recipe("酸香菇白菜", foods="白菜；香菇；醋；盐", label="晚餐、酸")
    constraints = Constraints(preferences=["酸"], preferred_ingredients=["香菇"])
    blocked = repair([previous], [previous, sour], constraints)
    assert blocked.recipes == [previous]
    improved = repair([previous], [previous, sour, covered], constraints)
    assert improved.recipes == [covered]


@pytest.mark.parametrize("label", ["午餐、酸", "酸"])
def test_known_meal_context_is_not_downgraded_to_other_or_unknown(label):
    previous = recipe("原白菜", label="晚餐")
    sour = recipe("酸味白菜", label=label)
    result = repair([previous], [previous, sour], Constraints(preferences=["酸"], meal_type="晚餐"))
    assert result.recipes == [previous]


def test_role_and_soup_positions_are_preserved_and_unknown_garlic_cue_is_not_coverage():
    previous = recipe("原白菜")
    protein = recipe("酸味鸡肉", foods="鸡肉；醋；盐", steps="鸡肉蒸熟装盘。", label="晚餐、酸")
    cue = recipe("醋蒜白菜", foods="白菜；蒜末；醋；盐")
    result = repair([previous], [previous, protein, cue])
    assert result.recipes == [previous] and not result.changed_positions
    assert any("未覆盖" in warning for warning in result.warnings)


def test_local_edit_only_touches_requested_slot_and_reports_protected_gap():
    first = recipe("原白菜一")
    second = recipe("原白菜二")
    sour = recipe("醋溜白菜", label="晚餐、酸")
    result = repair([first, second], [first, second, sour], replace_slot=2)
    assert result.recipes == [first, sour] and result.changed_positions == frozenset({1})
    protein = recipe("原鸡肉", foods="鸡肉；盐", steps="鸡肉蒸熟装盘。")
    protected = repair([first, protein], [first, protein, sour], replace_slot=2)
    assert protected.recipes == [first, protein]
    assert any("第 2 道" in warning and "未覆盖" in warning for warning in protected.warnings)


def test_empty_continuation_keeps_accepted_edit_even_when_global_alternative_exists():
    first = recipe("原白菜一")
    accepted = recipe("已接受白菜二")
    sour = recipe("醋溜白菜", label="晚餐、酸")
    result = repair([first, accepted], [first, accepted, sour], allow_repair=False)
    assert result.recipes == [first, accepted] and not result.changed_positions
    assert any("未覆盖" in warning and "保留已接受" in warning for warning in result.warnings)


def test_only_passed_hard_allowed_candidates_can_be_used_no_rule_or_global_search_call(monkeypatch):
    from app.rules.engine import RuleEngine

    def fail_if_called(*args, **kwargs):
        raise AssertionError("repair must not perform another rule or model call")

    monkeypatch.setattr(RuleEngine, "evaluate", fail_if_called)
    previous = recipe("原白菜")
    excluded = recipe("蒜香花生白菜", foods="白菜；花生；盐", label="晚餐、蒜香")
    safe = recipe("无花生蒜香白菜", foods="白菜；盐", label="晚餐、蒜香")
    result = repair(
        [previous], [previous, safe], Constraints(preferences=["蒜香"], allergies=["花生"])
    )
    assert result.recipes == [safe] and excluded not in result.recipes


def test_source_label_stronger_than_title_for_same_coverage_and_order():
    previous = recipe("原白菜")
    title = recipe("蒜香白菜")
    labeled = recipe("标签白菜", label="晚餐、蒜香")
    result = repair([previous], [previous, title, labeled], Constraints(preferences=["蒜香"]))
    assert result.recipes == [labeled]


def test_multiple_swaps_strictly_gain_coverage_converge_and_keep_role_counts():
    first = recipe("原白菜一")
    second = recipe("原白菜二")
    sour = recipe("酸味白菜", label="晚餐、酸")
    garlic = recipe("蒜香白菜")
    constraints = Constraints(preferences=["蒜香", "酸"], dish_count=2)
    pool = [first, second, sour, garlic]
    result = repair([first, second], pool, constraints)
    assert {r.recipe_id for r in result.recipes} == {sour.recipe_id, garlic.recipe_id}
    assert len(result.changed_positions) <= len(
        supported_flavor_preferences(constraints.preferences)
    )
    assert Counter(c for r in result.recipes for c in r.categories) == Counter({"vegetable": 2})
    retry = repair(result.recipes, pool, constraints)
    assert retry.recipes == result.recipes and not retry.changed_positions


@pytest.mark.parametrize("slot", [0, 2])
def test_invalid_local_slot_is_rejected_not_expanded_to_other_positions(slot):
    previous = recipe("原白菜")
    with pytest.raises(ValueError, match="existing menu slot"):
        repair([previous], [previous], replace_slot=slot)


def test_no_positive_flavor_request_never_moves_menu_or_pretends_unknown_match():
    previous = recipe("原白菜")
    sour = recipe("醋溜白菜", label="晚餐、酸")
    result = repair([previous], [sour], Constraints(preferences=["不要甜", "护心", "喜欢香菇"]))
    assert result.recipes == [previous] and not result.changed_positions
    assert any("不要甜" in warning for warning in result.warnings)
    assert any("暂无可靠口味映射" in warning for warning in result.warnings)
    assert not any("已覆盖" in warning for warning in result.warnings)


def test_changed_source_sharing_an_id_is_rejected_before_matching():
    previous = recipe("原白菜")
    forged = previous.model_copy(update={"raw_label": "酸"})
    with pytest.raises(ValueError, match="different source"):
        repair([previous], [forged])
