"""Literal source counterexamples: unknown sauce is not a named allergen."""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.models import Constraints, Ingredient, Recipe
from app.rules.engine import RuleEngine
from app.rules.sauce_composition import unresolved_sauce_evidence
from tests.test_component_slots import catalog, dish


def card(steps: str, *, generic_ingredient: bool = False) -> Recipe:
    return Recipe(
        recipe_id="authored-sauce",
        name="蒸豆腐",
        raw_ingredients="豆腐200克",
        ingredients=[Ingredient(name="豆腐", raw="豆腐200克")]
        + ([Ingredient(name="酱料", raw="酱料适量")] if generic_ingredient else []),
        steps=steps,
        categories=["protein"],
        meal_types=["晚餐"],
        source_row=1,
        fingerprint="authored-sauce",
        eligible=True,
    )


@pytest.mark.parametrize("restriction", ["no_spicy", "allergy", "vegan"])
def test_source_pork_jelly_bare_dip_does_not_establish_restricted_compatibility(
    restriction,
):
    recipe = catalog()[1572]
    assert recipe.name == "皮冻" and "蘸酱即可" in recipe.steps
    rules = RuleEngine()
    assert unresolved_sauce_evidence(recipe, rules._known_foods)
    constraints = (
        Constraints(no_spicy=True)
        if restriction == "no_spicy"
        else (
            Constraints(allergies=["芝麻"])
            if restriction == "allergy"
            else Constraints(diet_mode="vegan")
        )
    )
    before = recipe.model_dump()
    assert not rules.evaluate(recipe, constraints).allowed
    assert recipe.model_dump() == before


@pytest.mark.parametrize(
    "steps",
    [
        "豆腐蒸熟后蘸酱即可食用。",
        "豆腐蒸熟后蘸酱吃。",
        "豆腐蒸熟后蘸酱。",
    ],
)
def test_bare_unknown_sauce_use_is_not_excused_by_short_source_wording(steps):
    assert not RuleEngine().evaluate(card(steps), Constraints(no_spicy=True)).allowed


@pytest.mark.parametrize(
    "steps",
    [
        "豆腐蒸熟后蘸酱油食用。",
        "豆腐蒸熟后不蘸酱。",
        "豆腐蒸熟后无需蘸酱。",
        "酱由生抽、醋组成。豆腐蒸熟后蘸酱即可食用。",
        "将生抽和醋调成酱。豆腐蒸熟后蘸酱即可食用。",
        "豆腐蒸熟后加入酱牛肉。",
    ],
)
def test_bare_sauce_boundary_retains_specific_foods_omissions_and_declared_recipe(
    steps,
):
    assert not unresolved_sauce_evidence(card(steps), RuleEngine()._known_foods)


@pytest.mark.parametrize("action", ["蘸", "加入", "淋上", "倒入", "拌入", "配"])
@pytest.mark.parametrize(
    "constraints",
    [
        Constraints(no_spicy=True),
        Constraints(preferences=["不辣"]),
        Constraints(allergies=["芝麻"]),
        Constraints(excluded_ingredients=["花生"]),
    ],
)
def test_actual_unqualified_sauce_cannot_establish_restricted_compatibility(
    action, constraints
):
    recipe = card(f"豆腐蒸熟后{action}少许酱料食用。")
    before = recipe.model_dump()
    decision = RuleEngine().evaluate(recipe, constraints)
    assert not decision.allowed
    assert "成分" in " ".join(decision.reasons)
    assert recipe.model_dump() == before


@pytest.mark.parametrize(
    "constraints", [Constraints(no_spicy=True), Constraints(allergies=["芝麻"])]
)
def test_actual_original_white_cut_chicken_with_unnamed_dip_is_not_verified(
    constraints,
):
    recipe = next(r for r in catalog().values() if r.name == "白切鸡")
    assert "蘸酱料" in recipe.steps
    assert not RuleEngine().evaluate(recipe, constraints).allowed


@pytest.mark.parametrize(
    "steps",
    [
        "豆腐蒸熟装盘，不蘸酱料。",
        "豆腐蒸熟装盘，不要加入酱料。",
        "豆腐蒸熟装盘，无需蘸酱料。",
        "豆腐蒸熟装盘。示例：蘸酱料。",
        "豆腐蒸熟装盘。是否需要蘸酱料？",
        "豆腐蒸熟装盘。解释为什么不用蘸酱料。",
        "豆腐蒸熟装盘。",
        "豆腐蒸熟后蘸生抽。",
    ],
)
def test_finite_omission_discussion_and_specific_sauce_are_not_unknown_uses(steps):
    recipe = card(steps)
    assert (
        RuleEngine()
        .evaluate(recipe, Constraints(no_spicy=True, allergies=["芝麻"]))
        .allowed
    )


@pytest.mark.parametrize(
    "definition",
    [
        "酱料由生抽、醋组成。",
        "将生抽和醋混合调成酱料。",
        "把生抽、醋拌成酱料。",
    ],
)
def test_finite_explicit_source_composition_can_resolve_the_same_prior_sauce(
    definition,
):
    recipe = card(definition + "豆腐蒸熟后蘸上述酱料。")
    assert (
        RuleEngine()
        .evaluate(recipe, Constraints(no_spicy=True, allergies=["芝麻"]))
        .allowed
    )


@pytest.mark.parametrize(
    "definition",
    [
        "示例：酱料由生抽、醋组成。",
        "不建议将生抽和醋调成酱料。",
        "酱料由生抽和神秘粉组成。",
        "酱料由生抽和醋等组成。",
        "蘸料由生抽、醋组成。",
    ],
)
def test_example_negation_partial_unknown_and_other_sauce_do_not_resolve(definition):
    assert (
        not RuleEngine()
        .evaluate(card(definition + "豆腐蒸熟后蘸酱料。"), Constraints(no_spicy=True))
        .allowed
    )


def test_later_definition_does_not_retroactively_resolve_an_earlier_addition():
    assert (
        not RuleEngine()
        .evaluate(
            card("豆腐蒸熟后蘸酱料。酱料由生抽、醋组成。"), Constraints(no_spicy=True)
        )
        .allowed
    )


def test_explicit_allergen_in_resolved_sauce_is_still_a_hard_failure():
    recipe = card("酱料由生抽、芝麻油组成。豆腐蒸熟后蘸酱料。")
    assert not RuleEngine().evaluate(recipe, Constraints(allergies=["芝麻"])).allowed


def test_unknown_generic_ingredient_cannot_be_excused_by_missing_action():
    assert (
        not RuleEngine()
        .evaluate(
            card("豆腐蒸熟装盘。", generic_ingredient=True), Constraints(no_spicy=True)
        )
        .allowed
    )


def test_unknown_sauce_is_not_deleted_without_restrictions_or_rewritten():
    recipe = card("豆腐蒸熟后蘸酱料。")
    before = recipe.model_dump()
    decision = RuleEngine().evaluate(recipe, Constraints())
    assert decision.allowed and any("成分" in w for w in decision.warnings)
    assert recipe.model_dump() == before


@pytest.mark.parametrize("mode", ["ovo_lacto_vegetarian", "vegan"])
def test_unknown_sauce_does_not_certify_a_vegetarian_or_vegan_recipe(mode):
    assert (
        not RuleEngine()
        .evaluate(card("豆腐蒸熟后蘸酱料。"), Constraints(diet_mode=mode))
        .allowed
    )


def test_new_definition_is_recomputed_without_trusting_fingerprint_or_title():
    recipe = card("酱料由生抽、醋组成。豆腐蒸熟后蘸酱料。")
    engine = RuleEngine()
    assert engine.evaluate(recipe, Constraints(no_spicy=True)).allowed
    recipe.steps = "豆腐蒸熟后蘸酱料。"
    recipe.name = "明确不辣健康菜"
    recipe.labels = ["清淡"]
    assert not engine.evaluate(recipe, Constraints(no_spicy=True)).allowed


def test_planner_excludes_unknown_sauce_and_preserves_local_scope():
    unsafe = card("豆腐蒸熟后蘸酱料。")
    good = card("豆腐蒸熟装盘。").model_copy(
        update={"recipe_id": "good", "name": "蒸豆腐2"}
    )
    engine = RuleEngine()
    result = MenuPlanner(engine).plan(
        [unsafe, good], Constraints(no_spicy=True, dish_count=1)
    )
    assert [r.recipe_id for r in result.recipes] == ["good"]
    rice = dish("蒸米饭", "大米100克；水200克", "大米蒸熟食用。")
    egg = dish("煮鸡蛋", "鸡蛋2个", "鸡蛋煮熟食用。")
    local = MenuPlanner(engine).plan(
        [good, egg],
        Constraints(no_spicy=True, dish_count=2),
        current=[unsafe, rice],
        replace_slot=2,
    )
    assert local.failure and not local.recipes and "其他菜位" in local.failure


@pytest.mark.parametrize(
    "steps",
    [
        "加入生抽5毫升，盐2克，调成料汁；豆腐蒸熟后淋上料汁。",
        "加入生抽和芝麻油，拌匀，制成酱汁待用；豆腐蒸熟后淋上酱汁。",
        "将蒜末和酱油混合均匀作酱汁；豆腐蒸熟后淋上酱汁。",
        "加入姜末、生抽、香油、白糖和盐，搅拌均匀制成调味汁；豆腐蒸熟后淋上调味汁。",
        "将生抽、料酒、鸡精混合均匀制好调味汁；豆腐蒸熟后加入调味汁。",
        "酱料由生抽、醋组成；豆腐蒸熟后淋上调好的酱汁。",
        "酱油和水煮开，取出备用做蘸酱汁；豆腐蒸熟装盘。",
        "称取5g食用油、蒸鱼豉油、蚝油、鱼露和白糖，取下小碗混匀为调味汁；豆腐蒸熟后淋上调味汁。",
    ],
)
def test_literal_source_preparation_lists_and_nominal_dip_are_not_missing_composition(
    steps,
):
    assert not unresolved_sauce_evidence(card(steps), RuleEngine()._known_foods)


@pytest.mark.parametrize(
    "steps",
    [
        "加入神秘粉、生抽，混合均匀制成料汁；豆腐蒸熟后淋上料汁。",
        "加入未知粉和加入醋，制成酱汁；豆腐蒸熟后淋上酱汁。",
        "酱料由生抽、醋组成；蘸料由白糖、水组成；豆腐蒸熟后淋上调好的酱汁。",
    ],
)
def test_do_not_borrow_partial_lists_or_ambiguous_preceding_sauces(steps):
    assert unresolved_sauce_evidence(card(steps), RuleEngine()._known_foods)


@pytest.mark.parametrize(
    "name",
    [
        "招财进宝—葱油鲍鱼",
        "蒜蓉粉丝蒸时蔬",
        "蟹柳蒸金针菇",
        "蒸肠粉",
        "姜汁菠菜",
        "蒸秋季时蔬",
        "蒜香牛肉蒸土豆",
    ],
)
def test_original_explicit_lists_are_not_new_unknown_sauce_rejections(name):
    recipe = next(r for r in catalog().values() if r.name == name)
    assert not unresolved_sauce_evidence(recipe, RuleEngine()._known_foods)


def test_composition_cache_observes_vocabulary_and_returns_unshared_lists():
    recipe = card("酱料由生抽、醋组成。豆腐蒸熟后蘸酱料。")
    rules = RuleEngine()
    assert not unresolved_sauce_evidence(recipe, rules._known_foods)
    rules._known_foods.remove("醋")
    evidence = unresolved_sauce_evidence(recipe, rules._known_foods)
    assert evidence
    evidence.clear()
    assert unresolved_sauce_evidence(recipe, rules._known_foods)


@pytest.mark.parametrize(
    "constraints",
    [
        Constraints(allergies=["豆类"]),
        Constraints(allergies=["小麦"]),
        Constraints(excluded_ingredients=["酱油"]),
    ],
)
def test_named_seasoned_sauce_does_not_remove_allergen_or_soy_sauce_exclusion_guards(
    constraints,
):
    recipe = card("酱料由蒸鱼豉油、醋组成；豆腐蒸熟后蘸酱料。")
    assert not RuleEngine().evaluate(recipe, constraints).allowed


def test_seasoned_sauce_is_not_an_ordinary_soy_sauce_inventory_alias():
    recipe = card("酱料由蒸鱼豉油、醋组成；豆腐蒸熟后蘸酱料。")
    recipe.ingredients.append(Ingredient(name="蒸鱼豉油", raw="蒸鱼豉油5g"))
    engine = RuleEngine()
    assert engine.canonical_food("蒸鱼豉油") != engine.canonical_food("酱油")
    assert "蒸鱼豉油" in engine.inventory_missing(recipe, ["豆腐", "酱油", "醋"])
