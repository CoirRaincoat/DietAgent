"""A drinking-soup slot must not be filled by a drained stir-fry title."""

import pytest

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog, dish


def test_original_live_squid_is_not_a_drinking_soup():
    source = catalog()[82]
    assert source.name == "清汤鱿鱼卷"
    assert "捞出鱿鱼花" in source.steps and "翻炒" in source.steps
    assert source.categories == ["protein"]
    assert is_main_meal_recipe(source)
    assert MenuPlanner(RuleEngine()).plan([source], Constraints(dish_count=1, soup_count=1)).failure
    assert (
        not MenuPlanner(RuleEngine())
        .plan([source], Constraints(dish_count=1, soup_count=0))
        .failure
    )


def test_stale_soup_metadata_cannot_fill_menu_or_suggestions():
    stale = catalog()[82].model_copy(update={"categories": ["soup"]})
    assert not is_main_meal_recipe(stale)
    assert MenuPlanner(RuleEngine()).plan([stale], Constraints(dish_count=1, soup_count=1)).failure
    actual = dish("白菜汤", "白菜100克；清水300克", "白菜加水煮熟，连汤盛入碗中即可食用。")
    assert stale not in replacement_candidates(
        [actual], [stale], "soup-source", constraints=Constraints(dish_count=1, soup_count=1)
    )


@pytest.mark.parametrize(
    "steps",
    [
        "鸡肉焯水后捞出，鸡肉炒熟装盘即可。",
        "鸡肉煮熟后沥干，再煎熟装盘即可。",
        "鸡肉焯水后捞出沥干，鸡肉烤熟后装盘即可。",
    ],
)
def test_executed_final_solid_body_overrides_soup_word(steps):
    assert dish("清汤鸡肉", "鸡肉200克；清水300克", steps).categories == ["protein"]


@pytest.mark.parametrize(
    "steps",
    [
        "鸡肉先炒熟，加入清水煮开，连汤盛入碗中即可食用。",
        "鸡肉煮熟，另将蒜末炒熟作浇头，加入汤中即可食用。",
        "鸡肉加水煮熟，不要炒熟，连汤盛入碗中即可食用。",
        "鸡肉加水煮熟，可以炒熟，连汤盛入碗中即可食用。",
        "鸡肉加水煮熟，参考‘鸡肉炒熟装盘’，连汤盛入碗中即可食用。",
        "鸡肉炒熟，再将鸡肉和清汤盛入碗中即可食用。",
        "鸡肉炒熟后加入清水，连汤盛入碗中即可食用。",
        "鸡肉焯水后捞出备用，接下来的做法待核。",
        "鸡肉不要捞出，鸡肉炒熟装盘即可。",
        "参考‘鸡肉捞出’，鸡肉炒熟装盘即可。",
    ],
)
def test_soup_body_serving_or_unexecuted_finish_is_not_demoted(steps):
    assert dish("鸡肉汤", "鸡肉200克；清水300克；大蒜10克", steps).categories == ["soup"]


def test_nonmeal_and_hotpot_policies_are_not_changed():
    assert dish("万能凉拌汁", "鸡肉200克；水300克", "鸡肉炒熟后装盘即可。").categories == [
        "component"
    ]
    hotpot = next(r for r in catalog().values() if r.name == "韩式部队火锅")
    assert RuleEngine().evaluate(hotpot, Constraints(soup_count=0)).allowed
    assert MenuPlanner(RuleEngine()).plan([hotpot], Constraints(dish_count=1, soup_count=1)).failure
    for changes in ({"no_spicy": True}, {"allergies": ["海鲜"]}, {"diet_mode": "vegan"}):
        assert not RuleEngine().evaluate(hotpot, Constraints(soup_count=0, **changes)).allowed


def test_solid_squid_keeps_allergy_nonspicy_and_vegan_screens():
    squid = catalog()[82]
    for changes in ({"allergies": ["海鲜"]}, {"diet_mode": "vegan"}):
        assert MenuPlanner(RuleEngine()).plan([squid], Constraints(dish_count=1, **changes)).failure
    spicy = dish(
        "清汤鱿鱼卷", "鱿鱼200克；水300克；辣椒10克", "鱿鱼焯水后捞出，加入辣椒炒熟装盘即可。"
    )
    assert MenuPlanner(RuleEngine()).plan([spicy], Constraints(dish_count=1, no_spicy=True)).failure


def test_live_stock_asparagus_cannot_be_a_replacement_drinking_soup():
    source = catalog()[1772]
    assert source.name == "上汤芦笋" and "高汤200克" in source.raw_ingredients
    assert "7分钟/红烧" in source.steps
    assert source.categories == ["vegetable"]
    stale = source.model_copy(update={"categories": ["soup"]})
    assert not is_main_meal_recipe(stale)
    assert MenuPlanner(RuleEngine()).plan([source], Constraints(dish_count=1, soup_count=1)).failure


def test_explicitly_served_stock_prefixed_soup_is_not_demoted():
    source = dish("上汤白菜", "白菜100克；高汤300克", "白菜加入高汤烧熟，连汤盛入碗中即可食用。")
    assert source.categories == ["soup"]
