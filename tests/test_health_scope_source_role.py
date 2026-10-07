"""Role/source consistency of category points, not nutrition certification."""

import pytest

from app.domain.health_evidence import HealthRule, health_evidence
from app.domain.models import Constraints
from app.nutrition.structured import analyze_recipe
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog, dish


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("南瓜馒头", "面粉800克；南瓜80克；水100毫升", "面粉南瓜揉成面团，做馒头蒸熟后食用。"),
        ("胡萝卜鸡肉", "鸡肉200克；胡萝卜20克", "鸡肉胡萝卜蒸熟装盘。"),
        ("白菜豆腐汤", "白菜50克；豆腐100克；水300毫升", "白菜豆腐煮汤后食用。"),
    ],
)
@pytest.mark.parametrize("goal", ["降压", "护心", "控糖", "降尿酸"])
def test_wrong_cached_vegetable_role_cannot_award_nonvegetable_category_bonus(
    name, foods, steps, goal
):
    source = dish(name, foods, steps)
    assert source.categories != ["vegetable"]
    cached = source.model_copy(update={"categories": ["vegetable"]})
    evidence = RuleEngine(experiment_category_scope=True).goal_evidence(cached, goal)
    assert evidence is not None and evidence.category_foods
    assert not evidence.category_rank_enabled
    # A soup or stale role retains declared tofu facts, not entrée credit.
    assert evidence.score == 0
    assert analyze_recipe(cached, Constraints(health_goals=[goal])).goal_matches[0].ingredient_names


def test_true_source_vegetable_with_matching_cache_keeps_category_reference():
    source = dish("蒸白菜", "白菜200克；水100毫升", "白菜蒸熟装盘食用。")
    evidence = RuleEngine(experiment_category_scope=True).goal_evidence(source, "降压")
    assert evidence is not None and evidence.category_rank_enabled and evidence.score == 2


def test_real_colored_bread_food_facts_survive_without_vegetable_entree_bonus():
    source = next(r for r in catalog().values() if r.name == "开花馒头")
    assert source.categories == ["staple"]
    old = RuleEngine().goal_evidence(source, "降压")
    new = RuleEngine(experiment_category_scope=True).goal_evidence(source, "降压")
    assert old is not None and new is not None
    assert old.category_foods == new.category_foods == ("熟南瓜块",)
    assert old.score == new.score == 0
    assert health_evidence(source, HealthRule(prefer_categories=("vegetable",))).score == 2
    stale = source.model_copy(update={"categories": ["vegetable"]})
    checked = RuleEngine(experiment_category_scope=True).goal_evidence(stale, "降压")
    assert checked is not None and checked.score == 0


def test_source_name_and_finish_role_changes_cannot_reuse_wrong_cached_bonus():
    source = dish("蒸南瓜", "南瓜200克；水100毫升", "南瓜蒸熟后装盘。")
    rule = HealthRule(prefer_categories=("vegetable",), scope_category_to_role=True)
    assert health_evidence(source, rule).score == 2
    # Same parsed/raw ingredients and cached role; the title and finish establish
    # a soup, so cached vegetable cannot be reused as a whole-dish ranking fact.
    changed = source.model_copy(update={"name": "南瓜汤", "steps": "南瓜加入水中煮汤后食用。"})
    evidence = health_evidence(changed, rule)
    assert evidence.category_foods == ("南瓜",) and not evidence.category_rank_enabled
    assert evidence.score == 0


@pytest.mark.parametrize("token", ["盐", "白糖", "猪油"])
def test_role_mismatch_withholds_only_category_bonus_not_cautions_or_preferred_food(token):
    source = dish(
        "南瓜燕麦饭", f"大米100克；燕麦50克；南瓜50克；{token}1克", "大米燕麦南瓜蒸熟后食用。"
    )
    cached = source.model_copy(update={"categories": ["vegetable"]})
    rule = HealthRule(
        prefer_categories=("vegetable",),
        prefer_terms=("燕麦",),
        prefer_methods=("蒸",),
        discourage_terms=(token,),
        scope_category_to_role=True,
    )
    evidence = health_evidence(cached, rule)
    assert evidence.category_foods == ("南瓜",)
    assert evidence.preferred_foods == ("燕麦",) and evidence.good_methods == ("蒸",)
    assert evidence.discouraged_foods == (token,) and evidence.score == 0


def test_nonmeal_source_cannot_get_a_category_bonus_from_stale_cached_role():
    source = dish("黄瓜汁", "黄瓜200克；水300毫升", "黄瓜榨汁后饮用。")
    cached = source.model_copy(update={"categories": ["vegetable"], "eligible": True})
    evidence = RuleEngine(experiment_category_scope=True).goal_evidence(cached, "降压")
    assert evidence is not None and evidence.category_foods == ("黄瓜",)
    assert not evidence.category_rank_enabled and evidence.score == 0


def test_source_dessert_label_changes_scope_but_cached_health_label_does_not():
    source = dish("蒸南瓜", "南瓜200克；水100毫升", "南瓜蒸熟后装盘。")
    rule = HealthRule(prefer_categories=("vegetable",), scope_category_to_role=True)
    assert health_evidence(source, rule).score == 2
    dessert = source.model_copy(update={"raw_label": "甜品、晚餐"})
    assert health_evidence(dessert, rule).score == 0
    forged = source.model_copy(update={"labels": ["降压", "护心", "甜品"]})
    assert health_evidence(forged, rule).score == 2  # Source label, not mutable cache.
