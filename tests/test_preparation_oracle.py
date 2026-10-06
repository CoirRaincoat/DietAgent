"""Independent finite source reviews reject self-certified meal assertions."""

import pytest

from evaluation.main_meal_oracle import main_meal_findings


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("椰子玛格丽特", "糖粉10克；黄油20克；低筋面粉60克", "面团按扁烘烤。"),
        ("山药酸奶盆栽", "山药50克；酸奶20克；饼干30克", "饼干粉撒在杯中。"),
        ("固元阿胶糕", "阿胶20克；冰糖10克；核桃30克", "蒸制冷却切块。"),
        ("木瓜炖银耳", "木瓜60克；银耳10克；冰糖20克；水500克", "炖熟。"),
        ("红豆薏米粥", "红豆100克；薏米50克；红糖20克", "加入红糖2分钟混合，结束后取出食用。"),
    ],
)
def test_reviewed_source_contrasts_fail_independently(name: str, foods: str, steps: str) -> None:
    assert main_meal_findings(name, foods, ["晚餐", "蛋白质菜"], steps=steps)


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("木瓜炖银耳", "木瓜100克；银耳20克；冰糖1克；鸡胸肉200克", "炖熟。"),
        ("红豆薏米粥", "红豆100克；薏米50克；红糖20克", "红豆薏米熬煮，加入糖混合后取出。"),
        ("红豆薏米粥", "熟红豆100克；熟薏米50克；红糖20克", "混合2分钟，装盘。"),
        ("椰子玛格丽特", "白菜100克；盐1克", "蒸熟。"),
        ("山药酸奶盆栽", "山药100克；鸡肉100克", "蒸熟。"),
        ("固元阿胶糕", "豆腐100克；鸡蛋100克", "蒸熟装盘。"),
    ],
)
def test_same_title_completed_or_different_source_is_not_blanket_rejected(
    name: str, foods: str, steps: str
) -> None:
    assert main_meal_findings(name, foods, [], steps=steps) == []


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("果味山药", "山药、蜂蜜、蓝莓酱", "山药泥装入裱花袋挤出。"),
        ("老陈皮百合山药炖银耳", "银耳、冰糖、山药、水", "锅中开始烹饪。"),
        ("红豆沙南瓜汤圆", "糯米粉、红豆沙", "包入馅，搓汤圆后煮制。"),
        ("儿童肉松", "猪肉、油", "撕成细条，用肉松智能程序加工。"),
        ("南瓜糯米球", "南瓜、糯米、白砂糖", "芝麻球入锅慢炸。"),
        ("红枣百合蒸南瓜", "南瓜、冰糖", "蒸好后淋糖浆。"),
        ("红枣蒸山药泥", "山药、白砂糖", "搅打后蒸制。"),
        ("桂花蜂蜜烤南瓜", "南瓜、蜂蜜、桂花酱", "蜂蜜和桂花酱混合，烹饪后刷上酱。"),
        ("蒸山药", "山药、红糖", "蒸煮后放入盘中，蘸红糖食用。"),
        ("冰糖银耳西瓜盅", "西瓜、银耳、冰糖", "煮制后装入瓜盅，加清水蒸制。"),
    ],
)
def test_new_replay_discoveries_cannot_self_certify_as_main_meals(
    name: str, foods: str, steps: str
) -> None:
    assert main_meal_findings(name, foods, ["晚餐", "正餐菜"], steps=steps)


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("果味山药", "山药、猪肉、盐", "蒸熟装盘。"),
        ("老陈皮百合山药炖银耳", "银耳、冰糖、山药、鸡肉", "炖熟。"),
        ("红豆沙南瓜汤圆", "面粉、猪肉", "包入馅蒸熟。"),
        ("儿童肉松", "猪肉、油", "炒熟装盘。"),
        ("南瓜糯米球", "南瓜、糯米、猪肉", "包肉馅蒸熟。"),
        ("红枣百合蒸南瓜", "南瓜、盐", "蒸熟。"),
        ("红枣蒸山药泥", "山药、盐", "蒸制。"),
        ("桂花蜂蜜烤南瓜", "南瓜、盐、蜂蜜", "烤熟后刷蜂蜜。"),
        ("蒸山药", "山药、盐", "蒸熟装盘。"),
        ("冰糖银耳西瓜盅", "西瓜、银耳、鸡肉、盐", "加水炖熟。"),
    ],
)
def test_new_replay_title_alone_does_not_establish_dessert_or_component(
    name: str, foods: str, steps: str
) -> None:
    assert not main_meal_findings(name, foods, steps=steps)
