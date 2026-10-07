"""Source role counterexamples and public contrasts, not whole-library accuracy."""

import pytest

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints
from app.domain.source_soups import finished_soup_evidence
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog, dish


def test_original_seafood_pumpkin_cup_is_soup_by_finished_source_not_title() -> None:
    record = catalog()[591]
    assert record.name == "南瓜海鲜盅"
    assert record.categories == ["soup"]
    evidence = finished_soup_evidence(
        record.name, (i.name for i in record.ingredients), record.steps
    )
    assert evidence is not None
    assert evidence.reason == "pureed_base_finished_in_soup_then_served"
    assert "汤中放入" in evidence.witness
    assert is_main_meal_recipe(record)


@pytest.mark.parametrize("stale_role", ["vegetable", "protein", "staple"])
def test_cached_non_soup_role_cannot_hide_soup_from_count_or_suggestions(stale_role: str) -> None:
    stale = catalog()[591].model_copy(update={"categories": [stale_role]})
    assert not is_main_meal_recipe(stale)
    assert MenuPlanner(RuleEngine()).plan([stale], Constraints(dish_count=1)).failure
    good = dish("蒸南瓜", "南瓜200克；水100毫升", "南瓜蒸熟后装盘即可。")
    assert stale not in replacement_candidates(
        [good], [stale], "public-fixed", constraints=Constraints(dish_count=1)
    )


@pytest.mark.parametrize("soups", [0, 1])
def test_real_source_counts_as_soup_not_an_extra_vegetable(soups: int) -> None:
    result = MenuPlanner(RuleEngine()).plan(
        [catalog()[591]], Constraints(dish_count=1, soup_count=soups)
    )
    assert bool(result.failure) is (soups == 0)
    assert result.recipes == ([] if soups == 0 else [catalog()[591]])


@pytest.mark.parametrize(
    "name,foods,steps,expected",
    [
        (
            "蔬菜盅",
            "胡萝卜100克；高汤300毫升",
            "胡萝卜打成汁。汤中加入菜泥煮开，调味关火即可食用。",
            "soup",
        ),
        ("海鲜盅", "虾仁100克；水300毫升", "汤中加入虾仁煮开，连汤盛入碗中即可食用。", "soup"),
        ("蒸南瓜盅", "南瓜200克；水100毫升", "南瓜蒸熟后装盘即可食用。", "vegetable"),
        ("高汤炒白菜", "白菜200克；高汤100毫升", "白菜加入高汤后炒熟装盘即可。", "vegetable"),
        (
            "南瓜盅",
            "南瓜200克；高汤300毫升",
            "南瓜打成汁，汤中加入南瓜煮开，捞出沥干后装盘即可食用。",
            "vegetable",
        ),
        (
            "南瓜盅",
            "南瓜200克；高汤300毫升",
            "南瓜打成汁，汤中加入南瓜煮开，收汁后装盘即可食用。",
            "vegetable",
        ),
        ("南瓜盅", "南瓜200克；高汤300毫升", "南瓜打成汁。汤中加入南瓜煮开后备用。", "vegetable"),
        ("南瓜盅", "南瓜200克；水100毫升", "汤锅中加入南瓜蒸熟后食用。", "vegetable"),
        ("胡萝卜汁", "胡萝卜100克；高汤100毫升", "胡萝卜打成汁后倒入杯中饮用。", "drink"),
        (
            "南瓜粥",
            "南瓜100克；小米50克；高汤300毫升",
            "南瓜打成汁。汤中加入小米煮开，连汤盛入碗中即可食用。",
            "staple",
        ),
    ],
)
def test_finished_soup_fallback_does_not_guess_from_stock_or_vessel(
    name: str,
    foods: str,
    steps: str,
    expected: str,
) -> None:
    assert dish(name, foods, steps).categories == [expected]


def test_new_soup_recognition_keeps_allergy_gate() -> None:
    result = MenuPlanner(RuleEngine()).plan(
        [catalog()[591]], Constraints(dish_count=1, soup_count=1, allergies=["虾"])
    )
    assert result.failure and not result.recipes


@pytest.mark.parametrize("verb", ["炒", "烧", "焖", "煎"])
def test_stock_prefixed_solid_dish_is_not_named_soup(verb: str) -> None:
    assert dish(
        f"高汤{verb}白菜", "白菜200克；高汤100毫升", f"白菜加高汤{verb}熟装盘。"
    ).categories == ["vegetable"]


def test_draining_inside_heat_witness_is_not_hidden_by_regex() -> None:
    record = dish(
        "南瓜盅",
        "南瓜100克；高汤300毫升",
        "南瓜打成汁，汤中加入南瓜捞出后将高汤煮开，连汤盛入碗中即可食用。",
    )
    assert (
        finished_soup_evidence(record.name, (i.name for i in record.ingredients), record.steps)
        is None
    )


def test_soup_witness_cannot_resurrect_preparation_component() -> None:
    record = dish(
        "万能凉拌汁",
        "胡萝卜100克；高汤300毫升",
        "胡萝卜打成汁，汤中加入菜泥煮开，连汤盛入碗中即可食用。",
    )
    assert record.categories == ["component"]
    assert not is_main_meal_recipe(record)


@pytest.mark.parametrize(
    "steps",
    [
        "大蒜打成泥。汤中加入胡萝卜煮开，调味关火即可食用。",
        "胡萝卜切块备用。另将大蒜打成泥。汤中加入胡萝卜煮开，调味关火即可食用。",
        "胡萝卜切片。汤中加入胡萝卜煮开，把汤倒入白菜盘中即可食用。",
    ],
)
def test_condiment_or_pouring_stock_does_not_supply_finished_soup_witness(steps: str) -> None:
    record = dish("胡萝卜盅", "胡萝卜100克；大蒜10克；高汤300毫升", steps)
    assert (
        finished_soup_evidence(record.name, (i.name for i in record.ingredients), record.steps)
        is None
    )
    assert record.categories == ["vegetable"]
