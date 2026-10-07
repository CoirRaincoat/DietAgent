"""Source finishing preferences and retained frying cautions, not clinical scores."""

import pytest

from app.domain.health_evidence import HealthEvidence, HealthRule, health_evidence
from app.domain.models import Ingredient, Recipe


def evidence(steps: str) -> HealthEvidence:
    """Supply authored source text with deliberately misleading steam metadata."""
    record = Recipe(
        recipe_id="synthetic-health-method",
        name="蒸测试菜",
        raw_ingredients="白菜",
        ingredients=[Ingredient(name="白菜", raw="白菜")],
        steps=steps,
        methods=["蒸"],
        source_row=1,
        fingerprint="synthetic",
    )
    return health_evidence(
        record,
        HealthRule(
            prefer_methods=("蒸", "煮", "炖", "焯"), discourage_methods=("炸", "油炸", "油煎")
        ),
    )


@pytest.mark.parametrize(
    "steps",
    [
        "准备蒸烤架，将白菜炒熟后装盘。",
        "先煮白菜，随后油炸至金黄，装盘。",
        "将白菜放进智能设备，选择普通蒸模式作为参考。",
        "白菜可以蒸或烤，装盘食用。",
        "将酱汁煮开备用，白菜切片装盘。",
    ],
)
def test_equipment_preparation_alternatives_never_earn_finishing_preference(steps: str) -> None:
    assert evidence(steps).good_methods == ()


@pytest.mark.parametrize(
    "steps,wanted",
    [
        ("白菜清蒸至熟，装盘。", ("蒸",)),
        ("白菜熬煮至熟，装盘。", ("煮",)),
        ("白菜炖煮至熟，装盘。", ("炖",)),
        ("白菜放入蒸箱，开始烹饪，完成后装盘。", ("蒸",)),
        ("白菜焯水后装盘。另加入生抽设置熬煮，将料汁淋到白菜上食用。", ("焯",)),
    ],
)
def test_supported_final_actions_supply_only_the_established_preference(
    steps: str, wanted: tuple[str, ...]
) -> None:
    assert evidence(steps).good_methods == wanted


@pytest.mark.parametrize(
    "steps,wanted",
    [
        ("白菜蒸熟，另将蒜末油炸成浇头，淋上食用。", ("炸", "油炸")),
        ("先油炸白菜，再蒸熟后装盘。", ("炸", "油炸")),
        ("白菜蒸熟后可选油煎至上色。", ("油煎",)),
        ("白菜蒸熟后可选油炸至上色。", ("炸", "油炸")),
        ("准备空气炸锅和油炸锅，白菜蒸熟后食用。", ()),
        ("不油炸，白菜蒸熟后食用。", ()),
        ("白菜煎熟后装盘。", ()),
    ],
)
def test_frying_exposures_and_optional_cautions_survive_without_equipment_false_positives(
    steps: str, wanted: tuple[str, ...]
) -> None:
    # Generic pan-frying never establishes a declared oil amount or oil frying.
    assert evidence(steps).bad_methods == wanted
