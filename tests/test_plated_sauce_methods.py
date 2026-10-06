"""A served body's heating is distinct from the subsequent poured sauce."""
import pytest

from app.agent.menu_balance import analyze_menu_balance, balance_summary
from app.api.presentation import build_card
from app.domain.cooking_methods import cooking_method_evidence, main_cooking_methods
from tests.test_component_slots import catalog, dish


def test_actual_jade_cod_source_steam_is_not_replaced_by_heating_its_spinach_gravy():
    source = next(recipe for recipe in catalog().values() if recipe.name == "翡翠鳕鱼")
    original = source.model_dump(mode="json")
    assert "蒸制6分钟" in source.steps and "菠菜汁烧开" in source.steps and "浇在鱼块上" in source.steps
    evidence = cooking_method_evidence(source)
    assert evidence.main_methods == ("蒸",)
    assert any(event.method == "烧" and event.role == "auxiliary" for event in evidence.events)
    assert {"蒸", "烧"} <= set(evidence.executed_methods)
    for event in evidence.events:
        assert source.steps[event.start:event.end] == event.text
    assert source.model_dump(mode="json") == original
    assert "蒸" in build_card(source).badges and "烧" not in build_card(source).badges
    assert balance_summary(analyze_menu_balance([source])).endswith("做法有蒸。")


@pytest.mark.parametrize("steps,wanted", [
    ("鱼块蒸6分钟。取出摆入盘中。菠菜汁烧开，勾芡汁。浇在鱼块上即可。", ("蒸",)),
    ("鱼块蒸6分钟。取出摆盘。另加水煮开，调成料汁。浇到鱼块上即可。", ("蒸",)),
    ("鱼块煎熟后装盘。另将水煮开，加盐和淀粉制成芡汁。浇在鱼上即可。", ("煎",)),
    ("鱼块蒸熟后装盘。鱼块放入锅中烧熟，再淋上料汁。", ("烧",)),
    ("鱼块蒸熟后装盘。另加水煮开，熬成汤后饮用。", ("煮",)),
    ("不要蒸鱼。菠菜汁烧开，勾芡汁。浇在鱼上即可。", ("烧",)),
    ("鱼块蒸熟后装盘。另加水煮开，备好料汁，但不要浇在鱼上。", ("煮",)),
    ("鱼块蒸熟后装盘。倒入食用油，烧至七成热，淋在鱼上。", ("蒸",)),
    ("鱼放入智能设备，开始烹饪。取出装盘。另锅倒入食用油，烧至七成热，淋在鱼上。", ()),
    ("鱼块蒸熟后装盘。另锅倒入鱼块和油，烧熟后淋上料汁。", ("烧",)),
    ("鱼块蒸熟后装盘。浇上蒸鱼豉油和烧的滚烫的热油即可。", ("蒸",)),
    ("鱼放入智能设备，开始烹饪。浇上蒸鱼豉油和烧的滚烫的热油即可。", ()),
    ("鱼块蒸熟后装盘。鱼块烧熟后淋上蒸鱼豉油和热油。", ("烧",)),
    ("鱼块蒸熟后装盘。不要浇上烧的滚烫的热油。", ("蒸",)),
])
def test_literal_plated_then_poured_sauce_not_body_recooking_or_unlinked_soup(steps, wanted):
    source = dish("公开自编鱼菜", "鱼肉200克；水50克；淀粉5克", steps)
    assert main_cooking_methods(source) == wanted


def test_actual_oyster_fish_program_unknown_cannot_be_certified_by_heating_its_poured_oil():
    source = next(recipe for recipe in catalog().values() if recipe.name == "生蚝&鲈鱼同烹")
    assert "开始烹饪" in source.steps and "烧至七成热" in source.steps
    assert main_cooking_methods(source) == ()


def test_actual_cured_fish_unknown_program_not_certified_by_poured_hot_oil_noun():
    source = next(recipe for recipe in catalog().values() if recipe.name == "葱香腊肠蒸鲈鱼")
    assert "开始烹饪" in source.steps and "烧的滚烫的热油" in source.steps
    assert main_cooking_methods(source) == ()
