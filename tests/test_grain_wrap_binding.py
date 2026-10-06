"""Public authored contrasts plus the locally exposed original duck record."""

import csv
from pathlib import Path

import pytest

from app.domain.grain_wrapping import grain_dough_wrapping
from app.infrastructure.data import normalize_recipes
from tests.test_source_health_replay_roles import record


@pytest.mark.parametrize(
    "steps",
    [
        "鸭子用锡纸包起来烤熟。面粉揉成面团擀成面饼煎熟，一起享用。",
        "鸭子用荷叶包上烤熟。面粉揉成面团擀成面饼煎熟配食。",
        "鸭子烤熟。面粉揉成面团擀成面饼煎熟；鸭肉包入荷叶装盘。",
        "鸭子烤熟。面粉揉成面团擀成面皮；肉馅包入豆腐皮蒸熟。",
        "鸭子烤熟。面粉揉成面团擀成面饼；说明馅料另用，不在本菜使用。",
        "鸭子烤熟。面粉揉成面团擀成面皮，不要包入肉馅，煎熟配食。",
        "鸭子烤熟。面粉揉成面团擀成面皮，可以包入肉馅，当前煎熟配食。",
        "鸭子烤熟。面粉揉成面团擀成面皮，示例：包入肉馅蒸熟。",
        "鸭子烤熟。面粉揉成面团擀成面皮，另一道菜的做法：包入肉馅蒸熟。",
        "鸭子烤熟。面粉揉成面团擀成面皮。用锡纸卷起鸭腿烤熟。",
    ],
)
def test_non_grain_wrapping_or_unasserted_filling_does_not_turn_duck_into_staple(
    steps: str,
) -> None:
    sample = record("烤鸭配薄饼", "鸭子1只；面粉200克；盐1克", steps)
    assert sample.categories == ["protein"]


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        (
            "虾尾小笼",
            "虾肉80克；猪肉80克；面粉150克",
            "面粉揉成面团，擀成面皮，包入肉馅和虾尾蒸熟。",
        ),
        (
            "小动物造型",
            "南瓜100克；面粉150克；酵母2克；豆沙馅50克",
            "南瓜加面粉揉成面团，发酵好后包入馅料蒸熟。",
        ),
        ("鸡肉小包", "鸡肉100克；面粉150克", "面粉揉成面团，面皮包入鸡肉后蒸熟。"),
        ("鸡肉小卷", "鸡肉100克；面粉150克", "面粉揉成面团，擀成面皮，卷起鸡肉后蒸熟。"),
        ("肉饺子", "猪肉100克；面粉150克", "面粉揉成面团，擀成面皮包入肉馅后煮熟。"),
        ("肉包", "猪肉100克；面粉150克", "面粉揉面团，肉馅包入面皮后蒸熟。"),
        ("披萨", "面粉150克；鸡肉100克；奶酪20克", "面粉揉成面团擀平，摆鸡肉奶酪烤熟。"),
        ("咸薄饼", "面粉150克；盐1克", "面粉揉成面团擀成面饼，煎熟。"),
        ("清炒鸡肉", "鸡肉100克；面粉3克", "鸡肉裹少许面粉炒熟。"),
    ],
)
def test_real_grain_outer_forms_and_other_meals_keep_roles(
    name: str, foods: str, steps: str
) -> None:
    sample = record(name, foods, steps)
    assert sample.categories == ["protein" if name == "清炒鸡肉" else "staple"]


def test_actual_duck_source_foil_wrap_is_not_a_dough_wrap() -> None:
    path = Path(__file__).resolve().parents[1] / "dataset/recipe_kb/recipes_sample_2000.csv"
    with path.open(encoding="gb18030", newline="") as stream:
        samples = normalize_recipes(csv.DictReader(stream))
    sample = next(r for r in samples.values() if r.name == "北京烤鸭")
    assert sample.source_row == 126
    assert "锡纸包起来" in sample.steps and "揉成光滑的面团" in sample.steps
    assert sample.categories == ["protein"]


@pytest.mark.parametrize("row", [251, 295, 442, 577, 598, 778, 898, 1172, 1805])
def test_exposed_source_grain_outer_forms_keep_their_staple_identity(row: int) -> None:
    path = Path(__file__).resolve().parents[1] / "dataset/recipe_kb/recipes_sample_2000.csv"
    with path.open(encoding="gb18030", newline="") as stream:
        samples = normalize_recipes(csv.DictReader(stream))
    sample = next(r for r in samples.values() if r.source_row == row)
    assert sample.categories == ["staple"]


@pytest.mark.parametrize(
    "steps",
    [
        "面粉揉成面团，擀成面皮，包入肉馅蒸熟。",
        "面粉揉成面团，面皮包入鸡肉后蒸熟。",
        "面粉揉成面团，肉馅包入面皮后蒸熟。",
        "面粉揉成面团，面皮平铺，卷起来蒸熟。",
    ],
)
def test_binding_witness_is_an_exact_source_span(steps: str) -> None:
    original = steps.replace("，", "，  ").replace("面皮", "面 皮")
    evidence = grain_dough_wrapping(original)
    assert evidence is not None
    assert original[evidence.start : evidence.end] == evidence.text
    assert "包" in evidence.text or "卷" in evidence.text


@pytest.mark.parametrize(
    "action",
    [
        "包入豆腐皮蒸熟",
        "包入荷叶蒸熟",
        "包入锡纸烤熟",
        "包入碗蒸熟",
        "不是面皮包入肉馅",
        "勿将面皮包入肉馅",
        "可以把面皮包入肉馅",
        "示例：面皮包入肉馅",
        "面皮旁边锡纸包入鸭肉",
    ],
)
def test_continuation_does_not_bind_another_container_or_unasserted_instruction(
    action: str,
) -> None:
    assert grain_dough_wrapping("面粉揉成面团，擀成面皮，" + action) is None
