"""Authored sweet-root contrasts; not clinical scores or independent holdout."""

import pytest

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints, Recipe
from app.domain.source_preparation import preparation_dessert_evidence
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine


def record(name: str, foods: str, steps: str) -> Recipe:
    """Build an authored recipe with deliberately ordinary meal source labels."""
    return next(
        iter(
            normalize_recipes(
                [{"名称": name, "食材清单": foods, "烹饪步骤": steps, "label": "午餐、晚餐、清淡"}]
            ).values()
        )
    )


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        (
            "金黄小圆",
            "南瓜200克；糯米300克；白砂糖30克；白芝麻50克；油300毫升",
            "南瓜蒸熟碾泥，加入白砂糖、糯米粉，揉成面团搓圆，裹芝麻，芝麻球慢炸至金黄。",
        ),
        (
            "点心小圆",
            "南瓜200克；糯米粉100克；白糖10克；芝麻20克",
            "南瓜蒸熟，加入糯米粉白糖，揉成面团搓圆，裹芝麻炸熟。",
        ),
        (
            "果香蒸块",
            "南瓜500克；红枣50克；百合50克；黄冰糖10克；水50克",
            "冰糖加水煮稠成糖浆，蒸好的南瓜红枣百合取出，将糖浆淋在上面。",
        ),
        (
            "根茎甜碗",
            "山药300克；白砂糖20克；红枣20克；水600克",
            "主锅加入山药和白砂糖搅打，倒入碗加入红枣，放入蒸锅蒸制。",
        ),
        (
            "花香烤块",
            "南瓜500克；蜂蜜30克；桂花酱10克",
            "南瓜放入设备开始烹饪。蜂蜜与桂花酱混合成酱，烹饪结束后刷上桂花酱。",
        ),
        ("果香芋碗", "南瓜300克；白糖20克；红枣20克", "南瓜与白糖搅打成泥，加入红枣蒸制。"),
        ("白根茎盘", "山药200克；红糖10克", "山药蒸熟放入盘中，蘸红糖食用。"),
        ("瓜盅", "西瓜1个；银耳1朵；冰糖20克", "银耳煮熟后放入西瓜，加入清水与冰糖，蒸制。"),
    ],
)
def test_sweet_preparation_cannot_fill_menu_or_suggestion_even_with_stale_roles(
    name: str, foods: str, steps: str
) -> None:
    sample = record(name, foods, steps)
    assert sample.categories == ["dessert"]
    assert preparation_dessert_evidence((i.name for i in sample.ingredients), steps)
    stale = sample.model_copy(update={"categories": ["vegetable"], "eligible": True})
    assert not is_main_meal_recipe(stale)
    assert MenuPlanner(RuleEngine()).plan([stale], Constraints(dish_count=1)).failure
    ordinary = record("清蒸南瓜", "南瓜200克", "蒸熟后装盘。")
    assert replacement_candidates([ordinary], [stale], "fixed") == []


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("蒸南瓜", "南瓜200克；红枣20克", "南瓜与红枣放入设备开始烹饪。"),
        ("烤南瓜", "南瓜200克；蜂蜜2克", "南瓜刷蜂蜜后烤熟装盘。"),
        ("咸甜蒸南瓜", "南瓜200克；冰糖1克；盐1克", "蒸熟后淋少量糖浆调味。"),
        ("山药肉泥", "山药200克；猪肉100克；白糖1克；红枣10克", "搅打后蒸制。"),
        ("奶香山药泥", "山药200克；牛奶20克；盐1克；白糖1克；红枣10克", "搅打后蒸制。"),
        ("原味山药泥", "山药200克；红枣10克", "搅打后蒸制。"),
        ("蒸山药", "山药200克；白糖2克；红枣10克", "山药切片后蒸熟。"),
        ("山药米饭", "山药200克；大米100克；白糖2克；红枣10克", "大米煮熟，山药搅打后蒸制。"),
        ("糯米饭", "糯米200克；白糖2克；南瓜100克", "糯米南瓜加水蒸熟。"),
        (
            "猪肉糯米球",
            "糯米粉200克；猪肉100克；白糖1克；芝麻20克",
            "揉成面团包肉馅搓圆裹芝麻炸熟。",
        ),
        (
            "南瓜馒头",
            "南瓜100克；面粉200克；白糖10克；酵母2克",
            "南瓜搅打成泥，面粉揉团发酵后蒸制。",
        ),
        ("桂花酱烤肉", "猪肉200克；蜂蜜10克；桂花酱10克", "蜂蜜与桂花酱混合，烤熟后刷酱。"),
        ("咸香花酱南瓜", "南瓜200克；盐1克；蜂蜜1克；桂花酱1克", "蜂蜜桂花酱混合，烤熟后刷酱。"),
        ("桂花蒸南瓜", "南瓜200克；桂花2克", "蒸熟后撒桂花装盘。"),
        ("咸味山药", "山药200克；红糖1克；盐1克", "蒸熟后蘸红糖盐调味。"),
        ("山药蘸酱", "山药200克；酱油2克", "蒸熟后蘸酱油食用。"),
        ("银耳西瓜鸡汤", "西瓜100克；银耳10克；鸡肉200克；冰糖1克", "加水炖煮。"),
    ],
)
def test_sweet_ingredient_or_root_title_alone_does_not_remove_ordinary_recipes(
    name: str, foods: str, steps: str
) -> None:
    sample = record(name, foods, steps)
    assert preparation_dessert_evidence((i.name for i in sample.ingredients), steps) == []
    assert is_main_meal_recipe(sample)
    assert (
        MenuPlanner(RuleEngine())
        .plan([sample], Constraints(dish_count=1, soup_count=int(name.endswith("汤"))))
        .failure
        is None
    )
