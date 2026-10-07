"""Finished source soup-program evidence, not water/title or nutrition inference."""

import pytest

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints
from app.domain.source_soups import finished_soup_evidence
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog, dish


def test_live_lunch_source_is_soup_by_timed_program_not_title_suffix():
    source = catalog()[1746]
    assert source.name == "章鱼莲子煲洪湖莲藕"
    evidence = finished_soup_evidence(
        source.name, (i.name for i in source.ingredients), source.steps
    )
    assert evidence and evidence.reason == "timed_soup_program_without_solid_finish"
    assert "2小时/煲汤" in evidence.witness
    assert source.categories == ["soup"]
    for count in (0, 1):
        result = MenuPlanner(RuleEngine()).plan(
            [source], Constraints(dish_count=1, soup_count=count)
        )
        assert bool(result.failure) is (count == 0)


@pytest.mark.parametrize("role", ["protein", "vegetable", "staple"])
def test_stale_cached_role_cannot_hide_program_soup_in_menu_or_suggestion(role):
    stale = catalog()[1746].model_copy(update={"categories": [role]})
    assert not is_main_meal_recipe(stale)
    c = Constraints(dish_count=1, soup_count=0)
    assert MenuPlanner(RuleEngine()).plan([stale], c).failure
    good = dish("蒸鸡胸肉", "鸡胸肉200克；水50克", "鸡胸肉蒸熟装盘。")
    assert not replacement_candidates([good], [stale], "public", constraints=c)


@pytest.mark.parametrize("water", ["水", "矿泉水", "纯净水", "清水", "鸡汤"])
def test_explicit_timed_program_does_not_require_a_soup_title(water):
    record = dish(
        "莲藕鸡肉", f"莲藕200克；鸡肉150克；{water}700克",
        "鸡肉焯水后捞出沥干。加入鸡肉、莲藕和水，盖上锅盖，设置：1小时/煲汤。",
    )
    assert record.categories == ["soup"]


@pytest.mark.parametrize("steps", [
    "鸡肉加水，设置：1小时/煲汤。煮好后捞出沥干，再炒熟装盘。",
    "鸡肉加水，设置：1小时/煲汤。收汁后装盘。",
    "鸡肉加水，设置：1小时/煲汤。把汤倒掉，取鸡肉食用。",
    "鸡肉加水，设置：1小时/煲汤。过滤取出后蒸熟。",
    "鸡肉加水，不要设置：1小时/煲汤。鸡肉蒸熟。",
    "鸡肉加水，可以设置：1小时/煲汤。",
    "鸡肉加水，例如设置：1小时/煲汤。",
    "说明中写着‘设置：1小时/煲汤’，实际按屏幕操作。",
    "参考“设置：1小时/煲汤”，实际做法待核。",
    "鸡肉加水，选择煲汤模式。",
    "鸡肉放在煲汤锅里，蒸熟食用。",
])
def test_draining_optional_quoted_or_equipment_text_does_not_prove_finished_soup(steps):
    record = dish("莲藕鸡肉", "莲藕200克；鸡肉150克；水700克", steps)
    assert finished_soup_evidence(record.name, (i.name for i in record.ingredients), steps) is None


@pytest.mark.parametrize("tail", ["不要沥干，直接食用。", "参考‘沥干’二字，直接食用。"])
def test_negated_or_quoted_drain_is_not_a_solid_finish(tail):
    record = dish(
        "莲藕鸡肉", "莲藕200克；鸡肉150克；水700克",
        "加入鸡肉和水，设置：1小时/煲汤。" + tail,
    )
    assert record.categories == ["soup"]


def test_timed_soup_program_keeps_grain_form_and_component_precedence():
    assert catalog()[365].categories == catalog()[1526].categories == ["staple"]
    component = dish("万能凉拌汁", "胡萝卜100克；水300克", "加入胡萝卜和水，设置：1小时/煲汤。")
    assert component.categories == ["component"] and not is_main_meal_recipe(component)


def test_program_soup_does_not_relax_diet_allergy_or_nonspicy_constraints():
    source = catalog()[1746]
    for changes in ({"allergies": ["海鲜"]}, {"diet_mode": "vegan"}):
        c = Constraints(dish_count=1, soup_count=1, **changes)
        assert MenuPlanner(RuleEngine()).plan([source], c).failure
    spicy = dish("莲藕鸡肉", "鸡肉150克；莲藕200克；水700克；辣椒5克",
                 "加入鸡肉、辣椒和水，设置：1小时/煲汤。")
    assert MenuPlanner(RuleEngine()).plan([spicy], Constraints(dish_count=1, soup_count=1, no_spicy=True)).failure


def test_live_hotpot_is_not_a_drinking_soup_or_a_requested_soup_slot():
    source = next(r for r in catalog().values() if r.recipe_id == "recipe_0a76f64f40559ab2b6d5df00")
    assert source.name == "韩式部队火锅"
    assert "鸡汤500g" in source.raw_ingredients
    # User-approved meaning: no dedicated drinking soup, not no broth at all.
    # The legacy vegetable role is not certified here as a correct entree role.
    assert source.categories == ["vegetable"]
    zero = RuleEngine().evaluate(source, Constraints(soup_count=0))
    assert zero.allowed
    assert RuleEngine().evaluate(source, Constraints(soup_count=1)).allowed
    assert MenuPlanner(RuleEngine()).plan([source], Constraints(dish_count=1, soup_count=1)).failure
    for changes in ({"no_spicy": True}, {"allergies": ["海鲜"]}, {"diet_mode": "vegan"}):
        assert not RuleEngine().evaluate(source, Constraints(soup_count=0, **changes)).allowed


def test_hotpot_name_or_stock_alone_does_not_block_a_solid_finished_dish():
    record = dish("莲藕鸡肉火锅", "莲藕200克；鸡肉150克；高汤700克",
                  "加入鸡肉和高汤炖煮，取出鸡肉捞出沥干后炒熟装盘。")
    assert RuleEngine().evaluate(record, Constraints(soup_count=0)).allowed
