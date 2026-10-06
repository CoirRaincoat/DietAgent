"""Authored source conflicts, not sensory/clinical quality or measured oil limits."""

import pytest

from app.agent.menu_balance import analyze_menu_balance
from app.agent.planner import MenuPlanner
from app.agent.response_copy import response_facts
from app.domain.light_preparation import light_preparation_evidence
from app.domain.matching_tags import flavor_coverage, flavor_strength, matching_tags
from app.domain.models import Constraints, Intent, Recipe
from app.infrastructure.data import normalize_recipes
from app.retrieval.keyword import recipe_relevance_score
from app.rules.engine import RuleEngine


def sample(
    steps: str, foods: str = "鸡胸肉100克；盐1克；食用油5克", name: str = "清蒸鸡肉"
) -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": foods,
                        "烹饪步骤": steps,
                        "label": "晚餐、清淡",
                    }
                ]
            ).values()
        )
    )


@pytest.mark.parametrize(
    "steps",
    [
        "先油炸鸡肉，再蒸熟装盘。",
        "先将鸡肉煸炒，再加水煮熟装盘。",
        "先把鸡肉炒熟，再加水煮熟装盘。",
        "鸡肉蒸熟，另将蒜末油炸成浇头，淋上食用。",
        "鸡肉蒸熟后浇上热油，再淋蒸鱼豉油即可。",
        "鸡肉蒸熟。用油起锅，放入蒜末爆香，调成酱汁淋到鸡肉上。",
        "鸡肉蒸熟后可选油煎至上色。",
    ],
)
def test_source_clear_label_and_steam_finish_do_not_erase_whole_preparation(steps: str) -> None:
    recipe = sample(steps)
    snapshot = recipe.model_dump_json()
    assert any(e.tag == "清淡" for e in matching_tags(recipe).flavors)
    assert flavor_strength(recipe, "清淡") == flavor_coverage(recipe, ["清淡"]) == 0
    rules = RuleEngine()
    constraints = Constraints(preferences=["清淡"])
    assert rules.soft_goal_scores(recipe, constraints) == (0,)
    decision = rules.evaluate(recipe, constraints)
    assert decision.allowed  # Soft reference caution is not a new safety ban.
    assert any("清淡" in warning and "原步骤" in warning for warning in decision.warnings)
    assert recipe.model_dump_json() == snapshot


@pytest.mark.parametrize(
    "steps",
    [
        "不用油炸，鸡肉蒸熟后装盘。",
        "避免油炸，鸡肉蒸熟后装盘。",
        "鸡肉蒸熟后装盘，不淋热油。",
        "不要爆香，鸡肉加水煮熟即可。",
        "准备油炸锅和炒锅，鸡肉蒸熟后装盘。",
        "另一个菜例如“浇上热油”；本菜鸡肉蒸熟即可。",
        "油炸模式仅作参考，鸡肉加水煮熟即可。",
    ],
)
def test_negations_equipment_examples_do_not_withdraw_plain_source_reference(steps: str) -> None:
    recipe = sample(steps)
    assert flavor_strength(recipe, "清淡") == 3
    assert flavor_coverage(recipe, ["清淡"]) == 1
    assert RuleEngine().soft_goal_scores(recipe, Constraints(preferences=["清淡"])) == (1,)


def test_oil_salt_presence_and_unverified_quantity_do_not_prove_not_light() -> None:
    recipe = sample("鸡肉加水蒸熟装盘。", foods="鸡胸肉100克；盐1大勺；食用油适量")
    assert flavor_strength(recipe, "清淡") == 3
    assert RuleEngine().evaluate(recipe, Constraints(preferences=["清淡"])).allowed


def test_plain_stir_fry_source_label_is_not_blanket_banned() -> None:
    recipe = sample("白菜加少许油清炒至熟后装盘。", foods="白菜100克；食用油少许", name="清炒白菜")
    assert flavor_strength(recipe, "清淡") == 3
    assert RuleEngine().evaluate(recipe, Constraints(preferences=["清淡"])).allowed


def test_declared_fried_staple_topping_is_not_hidden_by_boiling() -> None:
    recipe = sample(
        "糯米蒸熟。猪肉加水煮熟，淋在饭上，撒上老油条碎即可。",
        foods="糯米150克；猪肉100克；老油条碎适量",
        name="猪肉糯米饭",
    )
    assert flavor_strength(recipe, "清淡") == 0
    assert RuleEngine().soft_goal_scores(recipe, Constraints(preferences=["清淡"])) == (0,)


def test_retrieval_and_planning_share_reference_filter_without_new_hard_ban() -> None:
    conflict = sample("先将鸡肉煸炒，再蒸熟装盘。", name="蒸炒鸡肉")
    plain = sample("鸡肉加水蒸熟装盘。", name="普通蒸鸡肉")
    rules = RuleEngine()
    constraints = Constraints(dish_count=1, meal_type="晚餐", preferences=["清淡"])
    assert recipe_relevance_score(plain, [], constraints, rules) > recipe_relevance_score(
        conflict, [], constraints, rules
    )
    assert MenuPlanner(rules).plan([conflict, plain], constraints).recipes == [plain]
    fallback = MenuPlanner(rules).plan([conflict], constraints)
    assert fallback.recipes == [conflict] and not fallback.failure
    assert any("清淡" in warning for warning in fallback.warnings)


def test_other_flavors_and_hard_allergy_nonspicy_constraints_remain_separate() -> None:
    recipe = sample("鸡肉油炸后蒸熟，装盘。", foods="鸡胸肉100克；蒜末5克；辣椒1个")
    recipe.raw_label = "清淡、蒜香、晚餐"
    assert flavor_coverage(recipe, ["清淡", "蒜香"]) == 0b10
    assert not RuleEngine().evaluate(recipe, Constraints(no_spicy=True)).allowed
    assert not RuleEngine().evaluate(recipe, Constraints(allergies=["鸡肉"])).allowed


def test_actual_appliance_mode_quote_is_not_an_example_exemption() -> None:
    recipe = sample("鸡肉放入设备，选择“油炸”模式，180℃烹饪10分钟，开始烹饪。")
    assert flavor_strength(recipe, "清淡") == 0
    assert light_preparation_evidence(recipe).cautions


def test_retained_conflict_is_disclosed_in_required_flavor_response_fact() -> None:
    recipe = sample("鸡肉蒸熟后浇上热油即可。")
    constraints = Constraints(dish_count=1, preferences=["清淡"])
    facts = response_facts(
        intent=Intent(action="explain"),
        constraints=constraints,
        diners=[],
        previous=[recipe],
        chosen=[recipe],
        balance=analyze_menu_balance([recipe], constraints),
    )
    assert "清淡参考待复核" in facts["flavor_preferences"]
    assert "原步骤包含浇上热油" in facts["flavor_preferences"]
    assert "口味来源参考已覆盖" not in facts["flavor_preferences"]


def test_cache_and_exact_source_spans_recheck_edited_steps() -> None:
    recipe = sample("鸡肉蒸熟后浇上热油即可。")
    evidence = light_preparation_evidence(recipe)
    assert all(recipe.steps[a:b] == text for a, b, text in evidence.source_spans)
    recipe.steps = "不淋热油，鸡肉蒸熟后装盘。"
    assert not light_preparation_evidence(recipe).cautions
    assert flavor_strength(recipe, "清淡") == 3


def test_fried_topping_assembly_is_not_hidden_by_last_generic_stir_fry() -> None:
    recipe = sample(
        "肉丝炒熟盛出。番茄炒成浆，加入肉丝翻炒。把浇头倒入米饭上即可。",
        foods="大米150克；鸡胸肉100克；番茄100克",
        name="米饭配肉菜浇头",
    )
    assert flavor_strength(recipe, "清淡") == 0
    assert light_preparation_evidence(recipe).cautions


def test_plain_stir_fry_with_later_nonfried_sauce_is_not_a_fried_topping() -> None:
    recipe = sample(
        "白菜清炒至熟。酱汁加水煮开，淋在白菜上即可。",
        foods="白菜100克；食用油少许；酱油少许",
        name="清炒白菜",
    )
    # Existing finishing parser can view sauce boil as auxiliary or final;
    # ordinary main stir-frying plus a nonfried sauce is not the new witness.
    assert flavor_strength(recipe, "清淡") == 3
