"""Bounded scene recognition/repair contrasts with explicit finite expectations."""

import pytest

from app.agent.scene_preferences import repair_scene_preferences
from app.domain.dining_scenes import (
    scene_request_clauses,
    scene_request_issues,
    supported_scene_preferences,
)
from app.domain.matching_tags import matching_tags
from app.domain.models import Constraints
from app.retrieval.keyword import KeywordRetriever
from app.rules.engine import RuleEngine
from tests.test_flavor_matching_integration import dish


@pytest.mark.parametrize(
    "text,expected",
    [
        ("家常", ("家常",)),
        ("这餐想要家常风格，可以调整。", ("家常",)),
        ("用餐场景：便当", ("便当",)),
        ("场景是宴客", ("宴客",)),
        ("不要便当但想要家庭聚餐", ("不要便当", "家庭聚餐")),
        ("这餐不是宴客，而是朋友聚餐。", ("不要宴客", "朋友聚餐")),
        ("家常不要", ("不要家常",)),
        ("场景：野餐", ("场景：野餐",)),
        ("如果是便当", ()),
        ("解释家庭聚餐", ()),
        ("便当是什么意思？", ()),
        ("朋友喜欢宴客，我不需要", ()),
        ("便当盒", ()),
        ("五个人吃晚餐", ()),
        ("上班族，护心，降压", ()),
        ("我说过‘宴客’，只是解释", ()),
    ],
)
def test_explicit_requests_not_mentions_or_inferred_population(text, expected):
    assert scene_request_clauses(text) == expected


@pytest.mark.parametrize(
    "preferences,people,expected",
    [
        (["家常"], 5, ("家常",)),
        (["家庭聚餐"], 5, ("家庭聚餐",)),
        (["宴客", "不要宴客"], 5, ()),
        (["一人食"], 5, ()),
        (["一人食", "家庭聚餐"], 1, ()),
        (["便当", "圣诞节"], 1, ("便当", "圣诞节")),
        (["场景：野餐"], 1, ()),
    ],
)
def test_conflicts_do_not_keep_old_ranking_credit(preferences, people, expected):
    assert supported_scene_preferences(preferences, people) == expected
    if not expected:
        assert scene_request_issues(preferences, people)


@pytest.mark.parametrize("name", ["非宴客蒸鱼", "不做宴客蒸鱼", "便当盒蒸鱼"])
def test_source_negation_and_container_name_not_scene_evidence(name):
    assert not matching_tags(dish(name, "鱼200克；盐1克")).scenes


def test_opt_in_scene_filter_uses_source_not_cache_and_never_falls_back():
    plain = dish("炒白菜", "白菜200克；盐1克").model_copy(update={"labels": ["家常"]})
    home = dish("家常炒菠菜", "菠菜200克；盐1克")
    retriever = KeywordRetriever([plain, home])
    constraints = Constraints(dish_count=1)
    assert retriever.search([], constraints, required_scenes=["家常"]) == [home]
    assert retriever.search([], constraints, required_scenes=["野餐"]) == []
    assert retriever.search([], constraints, required_scenes=["家常", "便当"]) == []
    assert set(r.recipe_id for r in retriever.search([], constraints)) == {
        plain.recipe_id,
        home.recipe_id,
    }


def repair(menu, candidates, constraints, *, scores=None, slot=None, allow=True):
    rules = RuleEngine()
    return repair_scene_preferences(
        menu,
        candidates,
        constraints,
        goal_scores=(
            scores
            if scores is not None
            else {r.recipe_id: rules.soft_goal_scores(r, constraints) for r in [*menu, *candidates]}
        ),
        order={r.recipe_id: i for i, r in enumerate(candidates)},
        food_matches=lambda r, t: bool(rules.food_matches(r, t)),
        replace_slot=slot,
        allow_repair=allow,
    )


def test_scene_swap_only_in_authorized_slot_and_stable_on_repeat():
    first = dish("炒白菜", "白菜200克；盐1克")
    second = dish("炒菠菜", "菠菜200克；盐1克")
    home = dish("家常炒青菜", "青菜200克；盐1克")
    constraints = Constraints(dish_count=2, preferences=["家常"])
    result = repair([first, second], [home], constraints, slot=2)
    assert result.recipes == [first, home] and result.changed_positions == {1}
    assert repair(result.recipes, [second, home], constraints, slot=2).recipes == result.recipes
    assert repair([first, second], [home], constraints, allow=False).recipes == [
        first,
        second,
    ]


@pytest.mark.parametrize(
    "scores", [{}, {"old": (2, 2), "new": (3, 1)}, {"old": (2, 2), "new": (3,)}]
)
def test_missing_vector_or_one_health_goal_regression_never_authorizes_scene_swap(
    scores,
):
    old = dish("蒸鱼", "鱼200克；盐1克")
    home = dish("家常蒸鸡", "鸡肉200克；盐1克")
    vectors = {r.recipe_id: scores[k] for r, k in [(old, "old"), (home, "new")] if k in scores}
    result = repair(
        [old],
        [home],
        Constraints(dish_count=1, preferences=["家常"], health_goals=["护心", "降压"]),
        scores=vectors,
    )
    assert result.recipes == [old]


def test_source_meal_evidence_cannot_be_worsened_for_scene_title():
    old = dish("蒸鱼", "鱼200克；盐1克", "晚餐")
    breakfast = dish("一人食蒸鸡", "鸡肉200克；盐1克", "早餐")
    assert repair(
        [old], [breakfast], Constraints(dish_count=1, preferences=["一人食"])
    ).recipes == [old]


def test_explicit_method_diversity_cannot_get_worse():
    old = dish("炒白菜", "白菜200克；盐1克")
    steamed = dish("蒸菠菜", "菠菜200克；盐1克").model_copy(update={"steps": "菠菜蒸熟装盘。"})
    home = dish("家常蒸青菜", "青菜200克；盐1克").model_copy(update={"steps": "青菜蒸熟装盘。"})
    assert repair(
        [old, steamed], [home], Constraints(dish_count=2, preferences=["家常", "做法多样"]), slot=1
    ).recipes == [old, steamed]


def test_sources_unchanged_and_different_source_for_same_id_is_rejected():
    old = dish("炒白菜", "白菜200克；盐1克")
    home = dish("家常炒菠菜", "菠菜200克；盐1克")
    before = [r.model_dump_json() for r in [old, home]]
    assert repair([old], [home], Constraints(dish_count=1, preferences=["家常"])).recipes == [home]
    assert before == [r.model_dump_json() for r in [old, home]]
    changed = old.model_copy(update={"name": "另一配方"})
    with pytest.raises(ValueError, match="identity"):
        repair([old], [changed], Constraints(dish_count=1, preferences=["家常"]))
    with pytest.raises(ValueError, match="slot"):
        repair([old], [home], Constraints(dish_count=1, preferences=["家常"]), slot=2)
