from app.agent.planner import MenuPlanner
from app.domain.models import Constraints, Ingredient, Recipe
from app.retrieval.keyword import KeywordRetriever
from app.rules.engine import RuleEngine


def recipe(key, ingredient, category="vegetable"):
    return Recipe(
        recipe_id=key, name=ingredient + "菜", raw_ingredients=ingredient,
        ingredients=[Ingredient(raw=ingredient, name=ingredient)],
        steps=ingredient + "煮熟。", source_row=1, fingerprint=key,
        categories=[category], methods=["煮"],
    )


def test_simple_menu_has_no_duplicates_and_soup_is_in_total_count():
    a, b, c, soup = recipe("a", "白菜"), recipe("b", "鸡蛋", "protein"), recipe("c", "米饭", "staple"), recipe("s", "冬瓜", "soup")
    result = MenuPlanner(RuleEngine()).plan([a, b, a, c, soup], Constraints(dish_count=3, soup_count=1))
    assert result.failure is None
    assert len(result.recipes) == len({r.recipe_id for r in result.recipes}) == 3
    assert sum("soup" in r.categories for r in result.recipes) == 1


def test_replacement_preserves_unrelated_valid_slots():
    a, b, c, d = recipe("a", "白菜"), recipe("b", "鸡蛋", "protein"), recipe("c", "米饭", "staple"), recipe("d", "豆腐", "protein")
    result = MenuPlanner(RuleEngine()).plan([a, b, c, d], Constraints(), current=[a, b, c], replace_slot=2)
    assert result.failure is None
    assert [r.recipe_id for r in result.recipes] == ["a", "d", "c"]
    assert [item["slot"] for item in result.changes] == [2]


def test_replacement_also_repairs_old_dishes_violating_new_constraint():
    a, b, c, d, e = recipe("a", "虾", "protein"), recipe("b", "鸡蛋", "protein"), recipe("c", "白菜"), recipe("d", "豆腐", "protein"), recipe("e", "米饭", "staple")
    result = MenuPlanner(RuleEngine()).plan(
        [a, b, c, d, e], Constraints(allergies=["海鲜"]), current=[a, b, c], replace_slot=2,
    )
    assert result.failure is None
    assert result.recipes[2].recipe_id == "c"
    assert not ({"a", "b"} & {r.recipe_id for r in result.recipes})
    assert {item["slot"] for item in result.changes} == {1, 2}


def test_rejected_ids_never_reappear_and_failure_is_explicit():
    candidates = [recipe("a", "白菜"), recipe("b", "鸡蛋", "protein")]
    result = MenuPlanner(RuleEngine()).plan(candidates, Constraints(dish_count=2), reject_ids={"a"})
    assert result.failure is not None and result.recipes == []


def test_inconsistent_soup_count_and_unknown_time_fail_without_menu():
    planner = MenuPlanner(RuleEngine())
    candidates = [recipe("a", "白菜")]
    assert planner.plan(candidates, Constraints(dish_count=1, soup_count=2)).failure
    assert planner.plan(candidates, Constraints(dish_count=1, max_minutes=10)).failure
    assert planner.plan(candidates, Constraints(dish_count=1), replace_slot=1).failure


def test_whole_catalog_fallback_can_find_safe_recipe_after_top_hit_rejected():
    shrimp, cabbage = recipe("a", "虾", "protein"), recipe("b", "白菜")
    constraints = Constraints(dish_count=1, allergies=["海鲜"])
    retriever = KeywordRetriever([shrimp, cabbage])
    planner = MenuPlanner(RuleEngine())
    top = retriever.search(["虾"], constraints, limit=1)
    assert planner.plan(top, constraints).failure
    full = retriever.search(["虾"], constraints)
    assert len(full) == 2
    result = planner.plan(full, constraints)
    assert result.failure is None
    assert result.recipes[0].recipe_id == "b"


def test_alias_retrieval_and_normal_meal_excludes_dessert_drink_components():
    tomato = recipe("z", "番茄")
    candidates = [recipe("a", "白菜"), tomato, recipe("b", "奶茶", "drink"), recipe("c", "蛋糕", "dessert"), recipe("d", "高汤", "component")]
    result = KeywordRetriever(candidates).search(["西红柿"], Constraints())
    assert result[0].recipe_id == "z"
    planned = MenuPlanner(RuleEngine()).plan(candidates, Constraints(dish_count=2))
    assert {r.recipe_id for r in planned.recipes} == {"a", "z"}

def test_multilabel_dessert_is_not_a_main_meal():
    dessert = recipe("cake", "鸡蛋", "dessert")
    dessert.categories = ["dessert", "protein", "staple"]
    result = MenuPlanner(RuleEngine()).plan([dessert], Constraints(dish_count=1))
    assert result.failure is not None
    assert result.recipes == []


def test_same_displayed_dish_is_not_selected_twice_or_used_as_replacement():
    a = recipe("a", "白菜")
    duplicate = recipe("another_id", "白菜")
    other = recipe("b", "豆腐", "protein")
    planner = MenuPlanner(RuleEngine())
    result = planner.plan([a, duplicate, other], Constraints(dish_count=2))
    assert result.failure is None
    assert len({r.name for r in result.recipes}) == 2
    changed = planner.plan([a, duplicate, other], Constraints(dish_count=1), current=[a], replace_slot=1)
    assert changed.failure is None
    assert changed.recipes[0].recipe_id == "b"

def test_required_labels_match_all_exact_labels_without_health_qualification():
    shrimp = recipe("shrimp", "虾", "protein")
    shrimp.labels = ["清淡", "午餐"]
    lunch_only = recipe("lunch", "白菜")
    lunch_only.labels = ["午餐"]
    light_only = recipe("light", "豆腐", "protein")
    light_only.labels = ["清淡"]
    constraints = Constraints(allergies=["海鲜"])
    matched = KeywordRetriever([shrimp, lunch_only, light_only]).search(
        [], constraints, required_labels=["清淡", "午餐"],
    )
    assert [item.recipe_id for item in matched] == ["shrimp"]
    assert not RuleEngine().evaluate(matched[0], constraints).allowed


def test_missing_required_label_does_not_fall_back_to_other_recipes():
    spicy = recipe("a", "白菜")
    spicy.labels = ["微辣", "午餐"]
    retriever = KeywordRetriever([spicy])
    assert retriever.search(["白菜"], Constraints(), required_labels=["辣"]) == []
    assert retriever.search(["白菜"], Constraints(), required_labels=["早餐"]) == []


def test_absent_or_empty_label_filter_preserves_full_catalog_fallback():
    a, b = recipe("a", "白菜"), recipe("b", "豆腐", "protein")
    a.labels = ["午餐"]
    retriever = KeywordRetriever([a, b])
    baseline = retriever.search(["库内没有的关键词"], Constraints())
    assert {item.recipe_id for item in baseline} == {"a", "b"}
    assert retriever.search([], Constraints(), required_labels=None) == baseline
    assert retriever.search([], Constraints(), required_labels=[]) == baseline


def test_four_dish_menu_prefers_two_vegetable_dishes_over_repeated_protein():
    protein_one = recipe("p1", "鸡肉", "protein")
    protein_one.methods = ["蒸"]
    protein_two = recipe("p2", "牛肉", "protein")
    protein_two.methods = ["炒"]
    vegetable_one = recipe("v1", "白菜", "vegetable")
    vegetable_one.methods = ["煮"]
    vegetable_two = recipe("v2", "西兰花", "vegetable")
    vegetable_two.methods = ["拌"]
    staple = recipe("s1", "米饭", "staple")
    staple.methods = ["焖"]

    result = MenuPlanner(RuleEngine()).plan(
        [protein_one, protein_two, vegetable_one, vegetable_two, staple],
        Constraints(dish_count=4),
    )

    assert result.failure is None
    assert sum("vegetable" in item.categories for item in result.recipes) == 2
    assert {"protein", "vegetable", "staple"} <= {
        category for item in result.recipes for category in item.categories
    }
