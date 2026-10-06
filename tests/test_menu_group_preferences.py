"""Positive meal-group preferences must not weaken food safety matching."""

import pytest

from app.agent.diners import diner_suitability
from app.agent.method_meal_tradeoff import find_tradeoff
from app.agent.planner import MenuPlanner
from app.domain.models import Constraints, Diner
from app.retrieval.keyword import recipe_relevance_score
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog, dish


def source(name):
    return next(r for r in catalog().values() if r.name == name)


@pytest.mark.parametrize("name,term", [
    ("基础煮燕麦饭", "主食"),
    ("蒸米饭", "主食"),
    ("姜汁菠菜", "绿叶菜"),
    ("蚕豆炒韭菜", "绿叶蔬菜"),
    ("蚕豆炒韭菜", "蔬菜"),
])
def test_declared_source_group_earns_existing_positive_preference_credit(name, term):
    rules = RuleEngine()
    recipe = source(name)
    plain = rules.evaluate(recipe, Constraints())
    wanted = rules.evaluate(recipe, Constraints(preferred_ingredients=[term]))
    assert plain.allowed and wanted.allowed
    assert wanted.score == plain.score + 3
    assert recipe_relevance_score(recipe, [], Constraints(preferred_ingredients=[term]), rules) == (
        recipe_relevance_score(recipe, [], Constraints(), rules) + 3
    )


@pytest.mark.parametrize("name,foods,steps,term", [
    ("清蒸菠菜", "菠菜200克；盐1克", "菠菜洗净蒸熟。", "绿叶菜"),
    ("清炒小白菜", "小白菜200克；油2克", "小白菜洗净炒熟。", "绿叶菜"),
    ("蒸米饭", "大米100克；水150克", "米洗净蒸熟。", "主食"),
    ("清炒西兰花", "西兰花200克；油2克", "西兰花洗净炒熟。", "蔬菜"),
])
def test_positive_group_match_has_source_evidence(name, foods, steps, term):
    recipe = dish(name, foods, steps)
    assert RuleEngine().preference_matches(recipe, term)


@pytest.mark.parametrize("name,foods,steps,term", [
    ("葱油蒸金针菇", "金针菇200克；葱花5克", "蒸熟金针菇后加葱花。", "绿叶菜"),
    ("菠菜鸡肉", "鸡肉200克；菠菜5克", "鸡肉蒸熟配菠菜。", "绿叶菜"),
    ("菠菜粉米饭", "大米100克；菠菜粉1克；水150克", "米蒸熟。", "绿叶菜"),
    ("菠菜汁蒸西兰花", "西兰花200克；菠菜5克", "西兰花蒸熟加菠菜汁。", "绿叶菜"),
    ("菠菜汤", "菠菜200克；水500克", "菠菜加水煮汤。", "绿叶菜"),
    ("清蒸菠菜", "西兰花200克；油2克", "西兰花蒸熟。", "绿叶菜"),
    ("蒸鸡肉", "鸡肉200克；淀粉5克", "鸡肉裹淀粉蒸熟。", "主食"),
    ("白糖蛋糕", "面粉100克；白糖100克；鸡蛋50克", "混合烤成蛋糕。", "主食"),
    ("蒸鸡肉", "鸡肉200克；盐1克", "鸡肉蒸熟，配喜欢的蔬菜。", "蔬菜"),
    ("菠菜汤", "菠菜200克；水500克", "菠菜加水煮汤。", "蔬菜"),
    ("蒸米饭", "大米100克；水150克", "米蒸熟后配蔬菜。", "蔬菜"),
])
def test_condiments_title_only_binders_soup_and_dessert_do_not_cover_groups(name, foods, steps, term):
    assert not RuleEngine().preference_matches(dish(name, foods, steps), term)


@pytest.mark.parametrize("update", [
    {"categories": ["protein"]}, {"eligible": False},
    {"quality_flags": ["unparsed_ingredients"]},
    {"name": "鸡肉卷", "steps": "鸡肉包裹蔬菜蒸熟。"},
])
def test_stale_or_unqualified_group_metadata_is_not_positive_evidence(update):
    recipe = source("姜汁菠菜").model_copy(update=update)
    assert not RuleEngine().preference_matches(recipe, "绿叶菜")


@pytest.mark.parametrize("term", ["主食", "蔬菜", "绿叶菜", "绿叶蔬菜"])
def test_groups_are_not_new_allergen_or_food_exclusion_exemptions(term):
    rules = RuleEngine()
    assert rules.unresolved_allergies(Constraints(allergies=[term])) == [term]
    assert rules.unresolved_exclusions(Constraints(excluded_ingredients=[term])) == [term]
    assert not rules.evaluate(source("姜汁菠菜"), Constraints(allergies=[term])).allowed
    assert not rules.evaluate(source("蒸米饭"), Constraints(excluded_ingredients=[term])).allowed


@pytest.mark.parametrize("extra,restrictions", [
    ("花生10克", {"allergies": ["花生"]}),
    ("辣椒5克", {"no_spicy": True}),
    ("猪油10克", {"diet_mode": "vegan"}),
    ("蒜10克", {"excluded_ingredients": ["蒜"]}),
])
def test_positive_group_credit_never_waives_existing_hard_food_requirements(extra, restrictions):
    recipe = dish("清炒菠菜", "菠菜200克；" + extra, "菠菜和所列原料炒熟。")
    rules = RuleEngine()
    assert rules.preference_matches(recipe, "绿叶菜")
    result = rules.evaluate(recipe, Constraints(preferred_ingredients=["绿叶菜"], **restrictions))
    assert not result.allowed and result.score == 0


def test_group_coverage_repairs_actual_leafy_gap_without_dropping_staple_or_fish_tofu():
    names = ["藕丁酿香菇", "葱油蒸金针菇", "鲈鱼蒸豆腐", "基础煮燕麦饭", "日式味增汤"]
    current = [source(name) for name in names]
    green = source("姜汁菠菜")
    rules = RuleEngine()
    constraints = Constraints(people=4, dish_count=5, soup_count=1, meal_type="晚餐",
        no_spicy=True, preferred_ingredients=["鱼", "豆制品", "绿叶菜", "主食"],
        preferences=["口味清淡"], health_goals=["护心"])
    plan = MenuPlanner(rules).plan([*current, green], constraints, current=current)
    assert plan.failure is None and len(plan.recipes) == 5
    assert sum("soup" in r.categories for r in plan.recipes) == 1
    assert all(rules.evaluate(r, constraints).allowed for r in plan.recipes)
    assert any(r.name == "姜汁菠菜" for r in plan.recipes)
    assert any(r.name == "基础煮燕麦饭" for r in plan.recipes)
    assert any(r.name == "鲈鱼蒸豆腐" for r in plan.recipes)
    assert not any("未覆盖食材偏好" in warning for warning in plan.warnings)


def test_local_change_and_readonly_continuation_cannot_rewrite_other_slots_for_a_group():
    current = [source(name) for name in ["葱油蒸金针菇", "鲈鱼蒸豆腐", "基础煮燕麦饭"]]
    rules = RuleEngine()
    constraints = Constraints(dish_count=3, soup_count=0, no_spicy=True,
        preferred_ingredients=["绿叶菜", "主食"])
    candidates = [*current, source("姜汁菠菜")]
    local = MenuPlanner(rules).plan(candidates, constraints, current=current, replace_slot=2)
    assert local.failure is None
    assert local.recipes[0] == current[0] and local.recipes[2] == current[2]
    readonly = MenuPlanner(rules).plan(candidates, constraints, current=current, recheck_soft_preferences=False)
    assert readonly.recipes == current
    assert any("未覆盖食材偏好：绿叶菜" in warning for warning in readonly.warnings)


def test_ordinary_food_matching_unchanged_and_no_false_label_only_group_retrieval_credit():
    recipe = source("蒸米饭")
    rules = RuleEngine()
    assert rules.preference_matches(recipe, "大米") == rules.food_matches(recipe, "大米")
    fake = source("葱油蒸金针菇").model_copy(update={"labels": ["绿叶菜"], "name": "绿叶菜金针菇"})
    assert recipe_relevance_score(fake, ["绿叶菜"], Constraints(), rules) == (
        recipe_relevance_score(fake, [], Constraints(), rules)
    )


def test_per_diner_group_disclosure_uses_same_source_coverage_without_changing_safety():
    diner = Diner(diner_id="public-friend", display_name="朋友",
        preferred_ingredients=["绿叶菜", "主食"], no_spicy=True)
    result = diner_suitability([source("姜汁菠菜"), source("基础煮燕麦饭")], [diner], RuleEngine())
    assert len(result) == 1 and result[0].hard_constraints_satisfied
    assert result[0].unmet_preferences == []


def test_meal_method_tradeoff_cannot_offer_losing_an_already_covered_leafy_dish():
    old = dish("晚餐煮菠菜", "菠菜200克；水20克", "菠菜煮熟装盘。")
    breakfast = dish("早餐蒸菠菜", "菠菜200克；水20克", "菠菜蒸熟装盘。")
    breakfast = breakfast.model_copy(update={"raw_label": "早餐", "labels": ["早餐"], "meal_types": ["早餐"]})
    non_leafy = dish("晚餐煮西兰花", "西兰花200克；水20克", "西兰花煮熟装盘。")
    constraints = Constraints(dish_count=1, meal_type="晚餐", preferences=["蒸"],
        preferred_ingredients=["绿叶菜"])
    result = find_tradeoff([old], [breakfast, non_leafy], constraints, RuleEngine())
    assert result is None or all(any(RuleEngine().preference_matches(r, "绿叶菜") for r in menu) for menu in result)
