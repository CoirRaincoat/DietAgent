"""Independent authored preparation contrasts discovered by source health replay."""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.models import Constraints, Recipe
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine


def record(name: str, foods: str, steps: str) -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [{"名称": name, "食材清单": foods, "烹饪步骤": steps, "label": "午餐、晚餐"}]
            ).values()
        )
    )


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        (
            "水果根茎拼盘",
            "山药200克；牛奶20克；蜂蜜10克；蓝莓酱10克",
            "山药蒸熟后碾碎，混合蜂蜜和蓝莓酱，装入裱花袋挤出造型。",
        ),
        (
            "山药百合银耳",
            "山药200克；百合30克；银耳20克；红枣10克；水500克；冰糖20克",
            "砂锅放入上述食材，装入设备，选择开始烹饪。",
        ),
        (
            "甜馅糯米球",
            "糯米粉100克；南瓜100克；红豆沙50克；水500克",
            "揉成面团，包入红豆沙馅，揉搓制圆形；水开后煮熟汤圆。",
        ),
    ],
)
def test_sweet_assemblies_cannot_fill_any_ordinary_meal_slot(
    name: str, foods: str, steps: str
) -> None:
    sample = record(name, foods, steps)
    assert sample.categories == ["dessert"]
    assert MenuPlanner(RuleEngine()).plan([sample], Constraints(dish_count=1)).failure


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        (
            "小动物造型",
            "南瓜泥100克；面粉200克；酵母2克；豆沙馅100克",
            "南瓜泥加入面粉和酵母揉成面团，发酵好后包入馅料；蒸熟。",
        ),
        (
            "虾尾小笼",
            "虾肉200克；猪肉200克；面粉300克",
            "面粉加水揉成面团，醒好后擀成面皮，包入肉馅和虾尾，蒸熟。",
        ),
        (
            "萝卜糕",
            "白萝卜200克；粘米粉100克；澄粉30克；盐1克",
            "粉类混成粉浆，加入萝卜后倒入模具，蒸熟。",
        ),
        ("肉汤圆", "糯米粉200克；猪肉100克；盐2克", "糯米粉揉成面团，包入肉馅，搓成汤圆，煮熟。"),
    ],
)
def test_explicit_grain_dough_and_batter_are_staples_not_incidental_food_roles(
    name: str, foods: str, steps: str
) -> None:
    sample = record(name, foods, steps)
    assert sample.categories == ["staple"]


def test_meat_floss_program_is_not_a_protein_entree() -> None:
    sample = record(
        "儿童肉丝",
        "猪里脊肉200克；食用油5克",
        "将肉块晾凉后撕成细条，加入油炒制，再设置肉松菜谱智能程序，结束后冷却即可食用。",
    )
    assert sample.categories == ["component"]


@pytest.mark.parametrize(
    "name,foods,steps,role",
    [
        (
            "银耳山药排骨汤",
            "银耳20克；山药100克；排骨200克；冰糖2克；盐2克；水500克",
            "放入砂锅炖熟。",
            "soup",
        ),
        ("牛奶蒸山药", "山药200克；牛奶20克；盐1克", "蒸熟后加入牛奶拌匀。", "vegetable"),
        ("蒸猪肉", "猪肉200克；面粉5克；盐1克", "混合少量面粉后蒸熟。", "protein"),
        ("裹糊猪肉", "猪肉200克；面粉50克；盐1克", "面粉调成粉浆，肉片裹上粉浆炸熟。", "protein"),
        (
            "北京烤鸭",
            "鸭子1只；面粉200克；盐1克",
            "烤鸭入烤箱烤熟。面粉加水揉成面团，擀成面饼后煎熟一起享用。",
            "protein",
        ),
        (
            "烤鸭配薄饼",
            "鸭子1只；面粉200克；盐1克",
            "鸭子包好入烤箱烤熟。另将面粉揉成面团，擀成面饼煎熟配食。",
            "protein",
        ),
        (
            "荷叶烤鸡",
            "三黄鸡1只；面粉100克；盐1克",
            "鸡用荷叶包好，面粉揉成面团擀成面皮包裹荷叶鸡，烤好后敲开面皮掀开荷叶。",
            "protein",
        ),
    ],
)
def test_savory_and_incidental_flour_contrasts_remain_available(
    name: str, foods: str, steps: str, role: str
) -> None:
    sample = record(name, foods, steps)
    assert sample.categories == [role]
    assert (
        MenuPlanner(RuleEngine())
        .plan([sample], Constraints(dish_count=1, soup_count=int(role == "soup")))
        .failure
        is None
    )
