"""Previously held-out PUBLIC counterexamples are now exposed development tests."""

import pytest

from app.api.presentation import build_card
from app.domain.cooking_methods import cooking_method_evidence, main_cooking_methods
from app.domain.health_evidence import HealthRule, health_evidence
from app.domain.models import Ingredient, Recipe


def source(steps: str) -> Recipe:
    """Do not let intentionally wrong cached tags or names supply method proof."""
    return Recipe(
        recipe_id="public-exposed-method",
        name="蒸炒拌测试菜",
        raw_ingredients="西兰花、鸡肉、蒜、孜然",
        ingredients=[Ingredient(raw=name, name=name) for name in ("西兰花", "鸡肉", "蒜", "孜然")],
        categories=["protein"],
        methods=["蒸", "焯", "炒", "拌"],
        steps=steps,
        source_row=1,
        fingerprint="public-exposed-source",
    )


@pytest.mark.parametrize(
    "steps,wanted",
    [
        ("西兰花先焯水，沥干后与蒜末炒熟，装盘。", ("炒",)),
        ("茭白先焯水，捞出后与蒜末炒熟，装盘。", ("炒",)),
        ("鸡胸肉与西葫芦炒熟，拌入孜然后装盘。", ("炒",)),
        ("鸡肉蒸熟，拌入胡椒粉后装盘。", ("蒸",)),
        ("将买来的熟制鸡肉蒸饺取出摆盘。", ()),
        ("将现成炒饭摆盘。", ()),
        ("将购来的烤面包切片装盘。", ()),
    ],
)
def test_exposed_finishing_counterexamples_keep_source_actions_not_word_occurrences(
    steps: str, wanted: tuple[str, ...]
) -> None:
    record = source(steps)
    evidence = cooking_method_evidence(record)
    assert evidence.main_methods == wanted
    assert record.steps == steps
    assert main_cooking_methods(record) == wanted
    for event in evidence.events:
        assert steps[event.start : event.end] == event.text


@pytest.mark.parametrize(
    "steps,wanted",
    [
        ("西兰花焯水后装盘。另炒蒜末制成浇头，淋上食用。", ("焯",)),
        ("鸡肉蒸熟，另炒蒜末做浇头，淋在鸡肉上食用。", ("蒸",)),
        ("鸡肉煮熟，放凉后拌入醋，凉拌均匀后装盘。", ("拌",)),
        ("鸡肉煮熟，放凉后拌入胡椒粉后装盘。", ("拌",)),
        ("蔬菜和酱汁拌匀后装盘。", ("拌",)),
        ("饺子包好，蒸饺子至熟后装盘。", ("蒸",)),
        ("将买来的熟制鸡肉蒸饺放入蒸锅，蒸热后装盘。", ("蒸",)),
        ("将现成米饭翻炒至熟后装盘。", ("炒",)),
        ("将购来的面包再烤至酥脆后装盘。", ("烤",)),
        ("把水煮开备用。黄瓜拌入盐后装盘。", ("拌",)),
        ("鸡肉炒熟装盘。黄瓜拌入醋后装盘。", ("拌",)),
        ("鸡肉炒熟后再拌入孜然后装盘。", ("炒",)),
    ],
)
def test_local_fix_does_not_erase_toppings_real_cold_mix_or_reheating(
    steps: str, wanted: tuple[str, ...]
) -> None:
    assert main_cooking_methods(source(steps)) == wanted


def test_seasoning_auxiliary_keeps_heating_exposure_and_does_not_claim_cold_badge() -> None:
    record = source("鸡肉油炸熟，拌入孜然后装盘。")
    evidence = cooking_method_evidence(record)
    assert evidence.main_methods == ("炸",)
    assert "炸" in evidence.executed_methods
    assert any(event.method == "拌" and event.role == "auxiliary" for event in evidence.events)
    assert "拌" not in build_card(record).badges
    result = health_evidence(record, HealthRule(discourage_methods=("炸", "油炸")))
    assert "炸" in result.bad_methods


def test_purchased_method_noun_never_earns_healthy_steam_reference() -> None:
    record = source("将买来的熟制鸡肉蒸饺取出摆盘。")
    assert "蒸" not in build_card(record).badges
    assert health_evidence(record, HealthRule(prefer_methods=("蒸",))).good_methods == ()
