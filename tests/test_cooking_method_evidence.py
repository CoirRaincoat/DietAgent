"""Finite authored finishing-action contrasts; no original recipes or APIs."""

import pytest

from app.domain.cooking_methods import cooking_method_evidence, main_cooking_methods
from app.domain.models import Ingredient, Recipe


def dish(steps: str) -> Recipe:
    """Supply synthetic text with deliberately untrustworthy method/title tags."""
    return Recipe(
        recipe_id="synthetic-method",
        name="蒸煮烤炒拌测试菜",
        raw_ingredients="鸡肉、豆腐",
        ingredients=[Ingredient(name="鸡肉", raw="鸡肉"), Ingredient(name="豆腐", raw="豆腐")],
        steps=steps,
        methods=["蒸", "煮", "烤", "炒", "拌"],
        source_row=1,
        fingerprint="synthetic",
    )


@pytest.mark.parametrize(
    "steps,wanted",
    [
        ("拌匀肉馅，装入烤肠模具。蒸熟后装盘。", ("蒸",)),
        ("蔬菜熬煮后滤出，加入鸡肉拌匀。蒸15分钟即可食用。", ("蒸",)),
        ("豆腐蒸熟，另炒蒜蓉和酱汁做浇头，淋在豆腐上食用。", ("蒸",)),
        ("肉蒸熟后煎至上色，装盘。", ("煎",)),
        ("把食材翻炒至熟，装盘。", ("炒",)),
        ("食材煸炒后即可食用。", ("炒",)),
        ("炖煮半小时后装盘。", ("炖",)),
        ("焖煮至熟后装盘。", ("焖",)),
        ("熬煮至熟即可食用。", ("煮",)),
        ("豆腐放入锅中红烧至熟，取出装盘。", ("烧",)),
        ("猪排放进烤箱，180℃烤10分钟至熟。", ("烤",)),
        ("选择普通蒸模式，100℃蒸12分钟，取出即可。", ("蒸",)),
        ("选择常规烘焙模式，以180℃烤熟。", ("烤",)),
        ("食材放入蒸箱，启动机器，烹饪完成后取出食用。", ("蒸",)),
        ("食材放入蒸锅，开始烹饪，完成后装盘。", ("蒸",)),
        ("鸡肉煮熟切丝，凉拌均匀后装盘。", ("拌",)),
        ("蔬菜冷拌装盘后，另将油烧热淋上食用。", ("拌",)),
        ("食材和酱汁拌匀，装盘食用。", ("拌",)),
        ("不用炸，食材蒸熟后食用。", ("蒸",)),
        ("不要蒸，改为烤熟后装盘。", ("烤",)),
        ("已经煮好的肉切片，煎至表面金黄后装盘。", ("煎",)),
        ("食材炸至金黄，装盘。", ("炸",)),
        ("食材油煎至熟，装盘。", ("煎",)),
        ("食材油炸至熟，装盘。", ("炸",)),
        ("食材蒸15分钟。取出后稍微冷却即可。", ("蒸",)),
        ("可以蒸或烤；本次明确蒸熟后装盘。", ("蒸",)),
    ],
)
def test_supported_finishing_action_ignores_equipment_preparation_and_topping(
    steps: str, wanted: tuple[str, ...]
) -> None:
    evidence = cooking_method_evidence(dish(steps))
    assert evidence.main_methods == wanted
    assert main_cooking_methods(dish(steps)) == wanted
    assert evidence.events
    for event in evidence.events:
        assert steps[event.start : event.end] == event.text


@pytest.mark.parametrize(
    "steps",
    [
        "",
        "烤肠模具备好，食材切碎。",
        "食材放在蒸烤架上备用。",
        "准备蒸盘、蒸箱、烤盘、炒锅。",
        "加入蒸鱼豉油，拌入肉馅备用。",
        "已煮好的菜切片备用。",
        "蒸熟的南瓜压成泥备用。",
        "不要蒸，也不要烤。",
        "可以蒸或烤至熟后食用。",
        "蒸或煮均可，完成后装盘。",
        "食材搅拌均匀后放入模具备用。",
        "将酱汁煮开备用。",
        "蒜末炒香后制成浇头备用。",
        "选择普通蒸模式，但尚未启动。",
        "食材放入智能设备，开始烹饪。",
        "食材放入烤箱，开始烹饪，取出食用。",
        "拌馅料后包饺子，先冷藏保存。",
        "蒸15分钟的做法可选，也可烤15分钟。",
        "食材放入蒸箱，但不要启动机器。",
        "选择普通蒸模式作为参考。",
        "准备空气炸锅与油炸锅，放入食材备用。",
        "食材煮熟，随后设置抄拌程序，装盘即可。",
    ],
)
def test_insufficient_optional_equipment_or_previously_cooked_text_stays_unknown(
    steps: str,
) -> None:
    assert main_cooking_methods(dish(steps)) == ()


def test_preparation_exposure_is_preserved_but_does_not_count_as_main_method() -> None:
    evidence = cooking_method_evidence(dish("先煮鸡肉，切片后蒸熟。另炒蒜蓉做浇头，淋上即可。"))
    assert evidence.main_methods == ("蒸",)
    assert set(evidence.executed_methods) == {"煮", "蒸", "炒"}
    assert any(event.role == "auxiliary" and event.method == "炒" for event in evidence.events)


def test_source_changes_recompute_even_with_same_id_and_stale_directory_tags() -> None:
    record = dish("蒸熟后装盘。")
    assert main_cooking_methods(record) == ("蒸",)
    record.steps = "煎熟后装盘。"
    assert main_cooking_methods(record) == ("煎",)


def test_title_and_directory_tags_never_supply_missing_source_actions() -> None:
    assert main_cooking_methods(dish("制作完成。")) == ()


@pytest.mark.parametrize(
    "steps,wanted",
    [
        ("白菜放入蒸烤盘/蒸烤架备用。", ()),
        (
            "白菜放入智能设备，开始烹饪。将蒸出的汁水倒入锅中，加入淀粉，大火煮成薄芡汁，淋在蒸好的白菜上食用。",
            ("蒸",),
        ),
        (
            "白菜放入智能设备，开始烹饪。烹饪结束取出，另烧热油，下蒜蓉爆香，加入生抽拌匀，淋到蒸好的白菜上即可享用。",
            ("蒸",),
        ),
        ("准备蒸熟的白菜，加入酱油装盘。", ()),
        ("不要启动机器。将料汁淋在蒸好的白菜上食用。", ()),
        ("白菜蒸好备用。将料汁淋在蒸好的白菜上食用。", ("蒸",)),
        ("白菜放入智能设备，开始烹饪。将酱汁淋到蒸熟的白菜上食用。", ("蒸",)),
        ("白菜放入智能设备，开始烹饪。淋在蒸过的白菜上食用。", ()),
    ],
)
def test_completed_dish_witness_after_local_execution_is_not_sauce_or_initial_ingredient(
    steps: str, wanted: tuple[str, ...]
) -> None:
    assert main_cooking_methods(dish(steps)) == wanted


@pytest.mark.parametrize(
    "steps,wanted",
    [
        (
            "白菜焯水，取出装盘。另加入蒜末、生抽和水，设置3分钟熬煮。将料汁淋在白菜上即可。",
            ("焯",),
        ),
        ("食材煮熟，取出装盘。另加水设置熬煮，烹饪结束将料汁均匀淋在食材上即可。", ("煮",)),
        ("白菜焯水后炒熟，取出装盘。", ("炒",)),
        ("白菜焯水或蒸熟均可。", ()),
        ("不要焯水，食材煮熟装盘。", ("煮",)),
        ("准备焯好的白菜，装盘即可。", ()),
    ],
)
def test_later_served_sauce_does_not_replace_a_verified_blanched_or_boiled_dish(
    steps: str, wanted: tuple[str, ...]
) -> None:
    assert main_cooking_methods(dish(steps)) == wanted
