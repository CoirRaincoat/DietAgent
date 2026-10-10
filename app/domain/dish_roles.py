"""Exclusive culinary roles, not ingredient nutrients or dietary guarantees.

Source names and preparations take precedence over incidental ingredients.
Fallback uses the first recognizable non-seasoning ingredient in source order;
that order is a heuristic, never an inferred mass ratio or portion size.
"""

import re
from collections.abc import Iterable

from app.domain.food_names import without_plant_animal_homonyms
from app.domain.grain_wrapping import grain_dough_wrapping
from app.domain.meal_roles import non_meal_roles
from app.domain.meat_enclosure import grain_in_meat_enclosure
from app.domain.protein_food_names import (
    ANIMAL_PROTEIN_FOODS,
    declared_protein_foods,
    named_declared_protein_foods,
)
from app.domain.source_soups import finished_soup_evidence, solid_soup_title_evidence
from app.domain.title_counts import culinary_title_without_zero_soup_metadata

ROLE_VERSION = "primary-culinary-role-v19-integrated-name-boundaries"
_PROTEIN = (
    "肉",
    "排骨",
    "鸡",
    "鸭",
    "鹅",
    "鱼",
    "虾",
    "蟹",
    "牛腩",
    "牛腱",
    "牛柳",
    "里脊",
    "蹄",
    "蛤",
    "蛏",
    "鲍",
    "生蚝",
    "牡蛎",
    "扇贝",
    "豆腐",
    "豆干",
    "腐竹",
    "百叶",
    "蛋",
    "牛排",
    "羊排",
    "羊腿",
    "小排",
    "牛仔骨",
    "肘",
    "鸽",
    "花甲",
    "豆皮",
    "香干",
    "豆花",
    "腊肠",
    "火腿",
    "培根",
    "猪",
    "凤爪",
    "龙骨",
    "筒骨",
    "青口贝",
    "银鲳",
    "东星斑",
    "骨头",
)
_FALSE_PROTEIN = (
    "鸡精",
    "鸡粉",
    "鸡汁",
    "鸡汤",
    "鸡高汤",
    "鸡味",
    "鸡肉粉",
    "鸡油",
    "牛肉粉",
    "牛肉汁",
    "牛肉汤",
    "牛肉高汤",
    "肉汤",
    "牛骨汤",
    "猪骨汤",
    "骨汤",
    "高汤",
    "鲍汁",
    "鲍鱼汁",
    "鱼露",
    "鱼油",
    "鱼豉油",
    "鱼香",
    "蚝油",
    "海鲜酱",
    "虾酱",
    "虾油",
    "虾粉",
    "蟹酱",
    "蟹粉调味",
    "猪油",
    "鸭油",
    "鹅油",
    "蛋黄酱",
    "鸡腿菇",
    "鸡枞",
    "鸡头米",
    "鱼腥草",
    "蟹味菇",
    "鸭梨",
    "牛肝菌",
    "马蹄",
)
_VEGETABLE = (
    "杏鲍菇",
    "鸡枞",
    "猪肚菇",
    "鸡毛菜",
    "白菜",
    "生菜",
    "青菜",
    "菠菜",
    "油麦",
    "西兰花",
    "花菜",
    "菜花",
    "芥蓝",
    "卷心菜",
    "包菜",
    "芹菜",
    "空心菜",
    "苋菜",
    "荠菜",
    "茄子",
    "瓜",
    "萝卜",
    "芦笋",
    "竹笋",
    "冬笋",
    "蘑菇",
    "香菇",
    "木耳",
    "秋葵",
    "莴笋",
    "菜心",
    "韭菜",
    "莲藕",
    "豆芽",
    "金针菇",
    "口蘑",
    "塔菜",
    "娃娃菜",
    "山药",
    "淮山",
    "海带",
    "苕尖",
    "春笋",
    "豇豆",
    "刀豆",
    "毛豆",
    "青椒",
    "尖椒",
    "蚕豆",
    "白玉菇",
    "蟹味菇",
    "鸡腿菇",
    "牛肝菌",
    "西红柿",
    "番茄",
)
_BROWN_RICE_FOODS = frozenset(("糙米", "熟糙米", "发芽糙米", "糙米粒", "糙米粉"))
_STAPLE_FOODS = (
    "蝴蝶面",
    "挂面",
    "意大利面",
    "意大利细面",
    "方便面",
    "豆面",
    "粳米",
    "籼米",
    "意大利米",
    "香米",
    "薏米",
    "薏仁",
    "高筋粉",
    "中筋粉",
    "低筋粉",
    "高粉",
    "中粉",
    "全麦粉",
    "小麦粉",
    "澄面",
    "澄粉",
    "粘米粉",
    "大黄米面",
    "烧麦皮",
    "馒头",
    "法棍",
    "面团",
    "手抓饼",
    "虾饺",
    "叉烧包",
    "肉夹馍",
    "大米",
    "米饭",
    "糯米",
    "面粉",
    "面条",
    "意面",
    "乌冬",
    "饺子皮",
    "馄饨皮",
    "吐司",
    "面包",
    "燕麦",
    "小米",
    "玉米",
    "土豆",
    "马铃薯",
    "红薯",
    "紫薯",
    "芋艿",
    "芋头",
    "杂粮",
    "粉丝",
    "粉条",
    "米粉",
    "年糕",
)
_STAPLE_FORMS = (
    "米饭",
    "炒饭",
    "盖饭",
    "焖饭",
    "烩饭",
    "面条",
    "意面",
    "蝴蝶面",
    "拉面",
    "炒面",
    "拌面",
    "乌冬",
    "馒头",
    "包子",
    "饺",
    "馄饨",
    "粥",
    "披萨",
    "比萨",
    "面包",
    "饭团",
    "米粉",
    "粉丝",
    "粉条",
    "年糕",
    "春卷",
    "煎饼",
    "烧饼",
    "吐司",
    "花卷",
    "法棍",
    "烧麦",
    "馍",
    "发糕",
    "米糕",
)
_ROOT_STAPLES = ("玉米", "土豆", "马铃薯", "红薯", "紫薯", "芋艿", "芋头", "杂粮")
_SEASONINGS = (
    "盐",
    "食盐",
    "水",
    "清水",
    "饮用水",
    "食用油",
    "植物油",
    "橄榄油",
    "花生油",
    "葵花籽油",
    "玉米油",
    "黄油",
    "芝麻油",
    "香油",
    "葱",
    "葱花",
    "小葱",
    "姜",
    "生姜",
    "姜末",
    "蒜",
    "大蒜",
    "蒜末",
    "蒜泥",
    "蒜蓉",
    "白糖",
    "白砂糖",
    "冰糖",
    "糖",
    "蜂蜜",
    "料酒",
    "生抽",
    "老抽",
    "酱油",
    "醋",
    "淀粉",
    "玉米淀粉",
    "土豆淀粉",
    "黑胡椒",
    "胡椒粉",
    "番茄酱",
    "豆瓣酱",
)


def _protein_tokens(food: str) -> set[str]:
    """Remove finite seasoning/plant false friends before looking for protein."""
    food = without_plant_animal_homonyms(food)
    for term in _FALSE_PROTEIN:
        food = food.replace(term, " ")
    return {term for term in _PROTEIN if term in food}


def _is_seasoning(food: str) -> bool:
    return (
        food in _SEASONINGS
        or any(term in food for term in _FALSE_PROTEIN)
        and not (_protein_tokens(food) or any(term in food for term in _VEGETABLE))
    )


def has_protein_ingredient(ingredient_name: str) -> bool:
    """Check finite ingredient evidence, independently of the whole-dish role.

    A soup or vegetable side can contain a protein source without filling the
    menu's protein-entrée slot. This predicate does not estimate its amount.
    """
    return bool(_protein_tokens("".join(ingredient_name.split())) or declared_protein_foods(ingredient_name))


def _staple_food(food: str) -> bool:
    # A literal source "米" means rice here, not every word ending in 米.
    # Brown-rice grain evidence is a finite whole ingredient, not every
    # product mentioning it (vinegar, tea, extract or protein supplement).
    return food == "米" or food in _BROWN_RICE_FOODS or any(
        term in food for term in _STAPLE_FOODS
    )


def _protein_wrap(foods: list[str], text: str) -> bool:
    """Require a declared non-grain wrapper and its local wrapping action.

    Tofu-skin wrappers establish a tofu preparation; vegetable leaves need a
    separate protein ingredient. This finite evidence does not measure filling
    ratios, servings or nutrients. An explicitly separate accompaniment cannot
    change the main dish's role.
    """
    soy = ("豆腐衣", "豆腐皮", "薄百叶", "百叶", "千张", "豆皮")
    leaves = ("白菜叶", "娃娃菜叶", "包菜叶", "卷心菜叶")
    for wrapper in (*soy, *leaves):
        declared = any(wrapper in food for food in foods)
        if wrapper in leaves:
            declared = any(wrapper.removesuffix("叶") in food for food in foods)
        if not declared:
            continue
        action = re.search(re.escape(wrapper) + r"[^。；;]{0,55}(?:包入|裹入|卷起|卷成)", text)
        if action is None or any(term in action.group() for term in ("另行", "另外", "另做")):
            continue
        if wrapper in soy or any(_protein_tokens(food) for food in foods):
            return True
    return False


def primary_dish_role(
    name: str, ingredient_names: Iterable[str], steps: str, labels: Iterable[str]
) -> str | None:
    """Choose one source-supported whole-dish role, leaving unknowns unset.

    Args:
        name: Original dish name, not generated presentation text.
        ingredient_names: Parsed ingredient names in source order.
        steps: Original preparation text.
        labels: Accepted source labels.

    Returns:
        One culinary role or None. ``vegetable`` can contain a meat garnish;
        ``protein`` includes eggs and tofu, not just meat. Neither promises a
        vegetarian diet, nutrition amounts, or adequate individual servings.
    """
    name = "".join(name.split())
    foods = ["".join(food.split()) for food in ingredient_names]
    non_meal = non_meal_roles(name, foods, steps, labels)
    if non_meal:
        return non_meal[0]

    # Count notes are not a culinary soup noun. Preserve the original title in
    # Recipe/identity and use only this bounded view for subsequent role rules.
    name = culinary_title_without_zero_soup_metadata(name)

    protein_evidence = set().union(*(_protein_tokens(food) for food in foods))
    egg_custard = ("蛋羹" in name or "蒸蛋" in name) and "蛋" in protein_evidence
    egg_wrapper = "蛋饺" in name and "蛋" in protein_evidence
    if egg_custard or egg_wrapper:
        return "protein"
    # A named filled rice roll has a grain-dish identity even with meat/egg
    # filling. Exact dedicated flour is evidence only for this named form;
    # flavoring/sauce/substitute mentions cannot invent a grain declaration.
    # An accompaniment after 配 does not rename the primary meat dish.
    rice_roll_form = name.split("配", 1)[0].endswith(("肠粉", "肠粉卷"))

    def grain_evidence(food: str) -> bool:
        # Declared brown rice does not establish a plated grain dish when the
        # finished title is a slurry. Preserve unknowns instead of promoting
        # source peanut/rice slurry to an ordinary main-meal staple slot.
        return (
            _staple_food(food) or rice_roll_form and food == "肠粉专用粉"
        ) and not (food in _BROWN_RICE_FOODS and name.endswith("浆"))

    staple_evidence = any(grain_evidence(food) for food in foods if not _is_seasoning(food))
    text = "".join(steps.split())
    flour = any(term in food for food in foods for term in ("面粉", "粘米粉", "糯米粉", "高筋粉", "中筋粉", "低筋粉"))
    formed_dough = (
        flour and grain_dough_wrapping(steps) is not None
        # A finished filled/rolled dough form, not a separate side pancake or
        # a chicken's disposable baking shell. Merely steaming another part of
        # a composite recipe cannot establish that its main entrée is a staple.
        and not any(term in text for term in ("敲开面皮", "敲开面壳", "剥去面皮", "去掉面壳"))
    )
    molded_batter = (
        flour and "模具" in text and "蒸" in text
        and any(term in text for term in ("粉浆", "混匀", "搅拌"))
        and "裹" not in text
    )
    if formed_dough or molded_batter or staple_evidence and "汤圆" in name:
        return "staple"
    # In this bounded construction 高汤 names the added stock, not the dish.
    # A stock-prefixed stir-fry is not an extra soup merely due to that noun.
    stock_cooked_title = re.match(r"^高汤(?:炒|烧|焖|煎)", name) is not None
    named_soup = ("汤" in name and not stock_cooked_title) or "羹" in name or name == "腌笃鲜"
    if named_soup and solid_soup_title_evidence(name, tuple(foods), steps) is None:
        return "soup"
    if finished_soup_evidence(name, foods, steps) is not None:
        return "soup"

    # Executed source geometry can outweigh a grain suffix: rice inside meat
    # is not rice enclosing meat. Soup, non-meal and formed outer dough retain
    # precedence. Original grain and chili evidence are never removed.
    if grain_in_meat_enclosure(foods, steps) is not None:
        return "protein"

    if (
        _protein_tokens(name.split("配", 1)[0]).intersection(protein_evidence)
        and (
            ("另将面粉" in text and "配食" in text)
            or ("配" in name and flour)
            or (
                "配" in name
                and any(food in _BROWN_RICE_FOODS for food in foods)
                and not any(term in name.split("配", 1)[0] for term in _STAPLE_FORMS)
                and not name.split("配", 1)[0].endswith(("饭", "面", "粽", "饼", "包"))
            )
        )
        and not formed_dough
    ):
        # An explicitly titled meat entrée '配' a separate grain side remains
        # a meat entrée. Actual bound outer-dough dishes and soups retain
        # precedence; no food amounts or whole-plate proportions are inferred.
        return "protein"

    staple_form = (
        rice_roll_form or any(term in name for term in _STAPLE_FORMS)
        or name.endswith(("饭", "面", "粽"))
    )
    if staple_form or staple_evidence and name.endswith(("饼", "包")):
        # A named grain dish without grain evidence is a source mismatch, not
        # permission to reinterpret it as a second protein entrée.
        return "staple" if staple_evidence else None

    if _protein_wrap(foods, text):
        return "protein"

    # The same finite food token must appear in the name AND an ingredient.
    # Thus a title mentioning fish does not turn a cabbage-only row into fish.
    if _protein_tokens(name).intersection(protein_evidence):
        return "protein"
    if named_declared_protein_foods(name, tuple(foods)) & ANIMAL_PROTEIN_FOODS:
        # A whole declared body such as 肥牛 may be absent from old substring
        # tokens. Named evidence cannot promote stock/sauce/unknown compounds;
        # finished soups, outer grain dishes and desserts retain precedence.
        return "protein"
    if any(term in name and any(term in food for food in foods) for term in _VEGETABLE):
        return "vegetable"
    # Explicit mixed-vegetable identity outranks the first incidental potato
    # or tofu. Require two distinct declared vegetable names; a generic title
    # alone is not evidence. Specific grain, soup, egg and meat identities have
    # already taken precedence. This does not imply vegetarian ingredients or
    # that any ingredient supplies a measured proportion of the dish.
    vegetable_aliases = {"菜花": "花菜", "包菜": "卷心菜", "淮山": "山药", "西红柿": "番茄"}
    vegetable_names = {
        vegetable_aliases.get(term, term) for term in _VEGETABLE
        if any(term in food for food in foods if not _is_seasoning(food))
    }
    if any(term in name for term in ("时蔬", "蔬菜拼盘")) and len(vegetable_names) >= 2:
        return "vegetable"
    if staple_evidence and any(term in name for term in _ROOT_STAPLES):
        return "staple"

    # Order is only a source heuristic; no comparison of incomplete quantities.
    for food in foods:
        if _is_seasoning(food):
            continue
        if _protein_tokens(food):
            return "protein"
        if grain_evidence(food):
            return "staple"
        if any(term in food for term in _VEGETABLE):
            return "vegetable"
    return None
