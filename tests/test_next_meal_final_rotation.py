"""Public local source contracts; no paid model, private data or gold score."""

from collections.abc import Sequence

import pytest

from app.agent.menu_variety import VarietyRepair
from app.agent.next_meal_rotation import repair_next_meal_repetition
from app.agent.planner import MenuPlanner
from app.agent.response_copy import required_fact_ids
from app.domain.models import Constraints, Intent, Recipe, ScopedMethod
from app.domain.source_soups import undrained_pot_reference
from app.infrastructure.data import normalize_recipes
from app.retrieval.keyword import recipe_relevance_score
from app.rules.engine import RuleEngine


def dish(
    name: str, foods: str, steps: str = "食材蒸熟装盘。", label: str = "晚餐、清淡"
) -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": foods,
                        "烹饪步骤": steps,
                        "label": label,
                    },
                ]
            ).values()
        )
    )


def repair(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    c: Constraints,
    query: Sequence[str] = (),
) -> VarietyRepair:
    rules = RuleEngine()
    records = [*menu, *candidates]
    return repair_next_meal_repetition(
        menu,
        candidates,
        c,
        recent_recipe_names=[[r.name for r in menu]],
        order={r.recipe_id: i for i, r in enumerate(candidates)},
        relevance=lambda r: recipe_relevance_score(r, query, c, rules),
        goal_scores={r.recipe_id: rules.soft_goal_scores(r, c) for r in records},
        goal_evidence=rules.goal_evidence,
        food_matches=lambda r, term: bool(rules.food_matches(r, term)),
        protected_food_terms=query,
    )


def test_final_repair_changes_real_source_role_not_only_equal_proxy_peers() -> None:
    old = dish("蒸鸡胸肉", "鸡胸肉200克；葱5克")
    new = dish("清蒸龙利鱼", "龙利鱼200克；姜5克")
    result = repair([old], [new], Constraints(dish_count=1, preferences=["清淡"]))
    assert result.recipes == [new] and result.changed_indices == {0}


@pytest.mark.parametrize(
    "guard",
    ["food", "query", "meal", "flavor", "goal", "body", "scene", "method", "scope"],
)
def test_final_repair_preserves_actual_requirements_and_source_references(
    guard: str,
) -> None:
    old = dish("蒸鸡胸肉", "鸡胸肉200克；葱5克")
    new = dish("清蒸龙利鱼", "龙利鱼200克；姜5克")
    c = Constraints(dish_count=1, preferences=["清淡"])
    query: Sequence[str] = ()
    if guard == "food":
        c.preferred_ingredients = ["鸡胸肉"]
    elif guard == "query":
        query = ("鸡胸肉",)
    elif guard == "meal":
        new = new.model_copy(update={"raw_label": "早餐、清淡"})
    elif guard == "flavor":
        new = new.model_copy(update={"raw_label": "晚餐、酸"})
    elif guard == "goal":
        c.health_goals = ["增肌"]
        new = dish("蒸猪肉", "猪肉200克；姜5克")
    elif guard == "body":
        old = new
        new = dish("蒸猪肉", "猪肉200克；姜5克")
        c.health_goals = ["护心"]
    elif guard == "scene":
        old = old.model_copy(update={"raw_label": "晚餐、清淡、家庭聚餐"})
        c.people = 4
        c.preferences += ["家庭聚餐"]
    elif guard == "method":
        c.preferences += ["蒸"]
        new = dish("煮龙利鱼", "龙利鱼200克；姜5克", "食材煮熟装盘。")
    elif guard == "scope":
        c.scoped_methods = [ScopedMethod(food="鸡胸肉", method="蒸", slot=1)]
    assert repair([old], [new], c, query).recipes == [old]


@pytest.mark.parametrize(
    "bad_source",
    [
        "name",
        "id",
        "role",
        "fingerprint",
        "primary_method",
        "no_steps",
        "no_ingredients",
        "no_raw",
        "ineligible",
        "unknown_method",
    ],
)
def test_novel_name_cannot_replace_traceable_source_or_role(bad_source: str) -> None:
    old = dish("蒸鸡胸肉", "鸡胸肉200克；葱5克")
    new = dish("清蒸龙利鱼", "龙利鱼200克；姜5克")
    updates: dict[str, dict[str, object]] = {
        "name": {"name": old.name},
        "id": {"recipe_id": old.recipe_id},
        "role": {"categories": ["staple"]},
        "fingerprint": {"fingerprint": old.fingerprint},
        "primary_method": {"ingredients": old.ingredients},
        "no_steps": {"steps": ""},
        "no_ingredients": {"ingredients": []},
        "no_raw": {"raw_ingredients": ""},
        "ineligible": {"eligible": False},
        "unknown_method": {
            "name": "龙利鱼成品",
            "steps": "选择开始烹饪按屏幕操作。",
            "methods": [],
        },
    }
    new = new.model_copy(update=updates[bad_source])
    if bad_source == "id":
        with pytest.raises(ValueError, match="identity"):
            repair([old], [new], Constraints(dish_count=1))
    else:
        assert repair([old], [new], Constraints(dish_count=1)).recipes == [old]


def test_hard_screening_precedes_repetition_even_with_no_alternative() -> None:
    old = dish("蒸鸡胸肉", "鸡胸肉200克；葱5克")
    spicy = dish("辣椒蒸龙利鱼", "龙利鱼200克；辣椒5克")
    allergy = dish("蒸花生鸡肉", "鸡胸肉200克；花生20克")
    unknown = dish("蘸酱蒸龙利鱼", "龙利鱼200克；姜5克", "食材蒸熟，蘸酱食用。")
    c = Constraints(dish_count=1, no_spicy=True, allergies=["花生"])
    result = MenuPlanner(RuleEngine()).plan(
        [old, spicy, allergy, unknown],
        c,
        recent_recipe_names=[[old.name]],
        allow_adjacent_rotation=True,
    )
    assert result.failure is None and result.recipes == [old]


def test_vegan_and_explicit_counts_still_apply_in_final_menu() -> None:
    old = dish("蒸豆腐", "豆腐200克；葱5克")
    animal = dish("清蒸龙利鱼", "龙利鱼200克；姜5克")
    c = Constraints(dish_count=1, vegetarian_dish_count=1, diet_mode="vegan")
    result = MenuPlanner(RuleEngine()).plan(
        [old, animal],
        c,
        recent_recipe_names=[[old.name]],
        allow_adjacent_rotation=True,
    )
    assert result.failure is None and result.recipes == [old]


@pytest.mark.parametrize(
    "retained,local,recheck,authorized",
    [
        (False, False, True, False),
        (True, False, True, True),
        (False, False, False, True),
        (True, True, True, True),
    ],
)
def test_final_repair_does_not_run_without_new_meal_authority(
    retained: bool,
    local: bool,
    recheck: bool,
    authorized: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = dish("蒸鸡胸肉", "鸡胸肉200克；葱5克")
    new = dish("清蒸龙利鱼", "龙利鱼200克；姜5克")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Final repetition repair is not authorized")

    monkeypatch.setattr("app.agent.planner.repair_next_meal_repetition", forbidden)
    MenuPlanner(RuleEngine()).plan(
        [old, new],
        Constraints(dish_count=1),
        current=[old] if retained else [],
        replace_slot=1 if local else None,
        recheck_soft_preferences=recheck,
        recent_recipe_names=[[old.name]],
        allow_adjacent_rotation=authorized,
    )


def test_tradeoff_fact_is_mandatory_without_model_fact_selection() -> None:
    assert "next_meal_tradeoff" in required_fact_ids(
        Intent(), {"next_meal_tradeoff": "可取舍"}
    )


@pytest.mark.parametrize(
    "tail",
    [
        "装盘食用。",
        "不要沥干，装盘食用。",
        "例如沥干后食用。",
        "参考“沥干”二字，直接食用。",
    ],
)
def test_no_soup_requires_final_pot_finish_not_a_preparatory_drain(tail: str) -> None:
    pot = dish(
        "笋干老鸭煲",
        "老鸭500克；笋干120克；水1500克",
        "鸭块焯水后捞出沥干。加入水和鸭块，设置90分钟/小火/烧煮。" + tail,
    )
    # Category is left untouched; this is an uncertainty gate, not new soup metadata.
    assert pot.categories == ["protein"]
    assert undrained_pot_reference(
        pot.name, (i.name for i in pot.ingredients), pot.steps
    )
    zero = RuleEngine().evaluate(pot, Constraints(soup_count=0))
    one = RuleEngine().evaluate(pot, Constraints(dish_count=3, soup_count=1))
    assert not zero.allowed and any("成品汤汁形态待核" in text for text in zero.reasons)
    assert one.allowed and any("未将其自动改判" in text for text in one.warnings)


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("笋干老鸭煲", "老鸭500克；水1500克", "鸭肉加入水煮熟，捞出沥干后装盘食用。"),
        ("笋干老鸭煲", "老鸭500克；水1500克", "鸭肉加入水煮熟，收汁后装盘食用。"),
        ("笋干老鸭煲", "老鸭500克；水1500克", "鸭肉不用煮，蒸熟装盘食用。"),
        ("盐水河虾", "河虾200克；水800克", "河虾加入水煮熟，取出食用。"),
        ("鸡肉煲仔饭", "鸡肉200克；大米100克；水150克", "食材加入水煮熟食用。"),
        ("蒸鸡肉煲", "鸡肉200克；水100克", "鸡肉蒸熟装盘食用。"),
        ("炒鸡肉煲", "鸡肉200克；高汤100克", "鸡肉加入高汤炒熟装盘食用。"),
        ("鸡肉煲", "鸡肉200克；葱5克", "鸡肉煮熟食用。"),
    ],
)
def test_pot_uncertainty_does_not_infer_soup_from_a_vessel_water_or_heat_alone(
    name: str,
    foods: str,
    steps: str,
) -> None:
    record = dish(name, foods, steps)
    assert (
        undrained_pot_reference(
            record.name, (i.name for i in record.ingredients), record.steps
        )
        is None
    )


def test_zero_soup_new_meal_does_not_fill_protein_slot_with_uncertain_broth_pot() -> (
    None
):
    old = dish("蒸鸡胸肉", "鸡胸肉200克；葱5克")
    pot = dish(
        "笋干老鸭煲",
        "老鸭500克；笋干120克；水1500克",
        "鸭肉加入水烧煮90分钟，出锅装盘食用。",
    )
    fish = dish("清蒸龙利鱼", "龙利鱼200克；姜5克")
    result = MenuPlanner(RuleEngine()).plan(
        [pot, old, fish],
        Constraints(dish_count=1, soup_count=0, preferences=["清淡"]),
        recent_recipe_names=[[old.name]],
        allow_adjacent_rotation=True,
    )
    assert result.failure is None and result.recipes == [fish]
