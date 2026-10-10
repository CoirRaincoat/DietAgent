"""Finite culinary evidence separating meal dishes from drinks and desserts.

These are role heuristics, not nutrition measurements or health judgements.
Unknown recipes remain unknown; ingredient presence alone does not establish
that a beverage or dessert is a vegetable, protein or staple dish.
"""

import re
from collections.abc import Iterable

from app.domain.food_names import without_plant_animal_homonyms
from app.domain.models import Recipe
from app.domain.slot_components import slot_component_evidence
from app.domain.source_preparation import (
    crumb_coated_entree,
    grain_completion_issue,
    meat_floss_component,
    preparation_dessert_evidence,
)
from app.domain.source_soups import finished_soup_evidence, solid_soup_title_evidence
from app.domain.title_counts import culinary_title_without_zero_soup_metadata

NON_MEAL_ROLES = frozenset({"dessert", "drink", "component"})
MAIN_MEAL_ROLES = frozenset({"vegetable", "protein", "staple", "soup"})


def is_non_meal_role_exclusion(value: str) -> bool:
    """Exact category words, not ingredient names or allergy exemptions.

    Keep the user's original exclusion in stored constraints. The ordinary
    meal planner already excludes drinks/desserts using source-aware roles;
    receiving these words in an ingredient-shaped field must not reject all
    meal dishes as unsupported foods. Unknown or compound names stay foods.
    """
    return value.strip() in {"甜品", "甜点", "饮料"}


_PREPARATION_NAMES = frozenset(
    {
        "冰糖粉",
        "乳化",
        "高温快煮",
        "68℃慢煮",
        "59℃慢煮",
        "派皮",
        "万能凉拌汁",
        "果蔬清洗",
        "蔬菜碎",
        "照烧汁",
        "蒜泥",
    }
)
_DESSERT_WORDS = (
    "蛋糕",
    "饼干",
    "布丁",
    "冰淇淋",
    "冰激凌",
    "泡芙",
    "马卡龙",
    "巧克力",
    "蛋挞",
    "糖水",
    "桃胶",
    "雪媚娘",
    "麻薯",
    "双皮奶",
    "糯米糍",
    "大福",
    "慕斯",
    "糍粑",
    "冰棍",
    "班戟",
    "拉糕",
    "糯米糕",
    "月饼",
    "司康",
    "钵仔糕",
    "甜甜圈",
    "达克瓦兹",
    "山楂糕",
    "枣糕",
    "阿胶糕",
    "甜汤",
    "甜羹",
    "奶冻",
    "茶冻",
    "沙冰",
    "冰沙",
    "黑芝麻糕",
    "马蹄糕",
    "梨挞",
)
_DRINK_SUFFIXES = (
    "茶",
    "奶昔",
    "豆浆",
    "咖啡",
    "果汁",
    "蔬汁",
    "饮",
    "饮品",
    "牛奶",
    "鲜奶",
    "拿铁",
    "星冰乐",
    "五谷浆",
    "气泡水",
)
_BEVERAGE_BASES = (
    "苹果",
    "梨",
    "西瓜",
    "橙",
    "橘",
    "柠檬",
    "百香果",
    "葡萄",
    "草莓",
    "蓝莓",
    "菠萝",
    "芒果",
    "黄瓜",
    "胡萝卜",
    "果蔬",
    "荔枝",
    "桂圆",
)
_SWEETENERS = ("白糖", "白砂糖", "砂糖", "糖粉", "冰糖", "蜂蜜", "红糖")
_SWEET_SOUP_BASES = (
    "绿豆",
    "红豆",
    "银耳",
    "莲子",
    "百合",
    "南瓜",
    "玉米",
    "紫薯",
    "黑芝麻",
    "梨",
    "红枣",
)
_SAVORY_FOODS = ("肉", "排骨", "鸡", "鸭", "鱼", "虾", "蟹", "牛腩", "火腿", "培根")
_SAVORY_SEASONINGS = ("盐", "酱油", "生抽", "老抽", "蚝油", "豆瓣酱")
_JUICE_ACTION = re.compile(r"榨汁|打浆|搅打|取汁")
_DRINKING_FINISH = re.compile(r"(?:饮用|喝)(?:即可)?$")
_NEGATED_DRINKING = re.compile(
    r"(?:不适合|不建议|不可|不能|不要|不宜|不应|不得|不是|并非|非供|禁止|勿|无需)"
    r"[^。；;]{0,14}(?:饮用|喝)"
)


def preparation_only_steps(steps: str) -> bool:
    """Detect explicit preparation-only records, not universal cooking readiness.

    Washing/cutting/marinating without an execution or finishing step is not a
    completed recipe. Cold mixing/serving and explicit equipment execution count;
    naming an oven, steamer or frying pan alone does not. Other unclear records
    are not certified complete by this finite negative-evidence check.
    """
    text = "".join(steps.split())
    preparation = any(
        word in text
        for word in ("食材准备", "洗净", "清洗", "切块", "切片", "切丝", "备好", "去皮", "腌制")
    )
    execution = re.search(
        r"蒸(?!锅|笼|架|盘|鱼豉油)|煮|炖(?!锅)|炒(?!锅)|烤(?!箱|盘|架)|煎|炸|焖"
        r"|凉拌|拌匀|搅拌|装盘|即可食用|尽情品尝|开始烹饪|开启烹饪|按下.*?烹饪",
        text,
    )
    return preparation and execution is None


def _finished_liquid_drink(name: str, steps: str, main_protein: bool) -> bool:
    """A final liquid-to-cup serving action; never a prep vessel or soup."""
    if main_protein or "汤" in name or "羹" in name or name.endswith(("饭", "面", "饼", "包", "粽")):
        return False
    final_step = re.split(r"第\s*\d+\s*步\s*[:：]", steps)[-1]
    cup = re.search(r"液体[^。；;\n]{0,12}倒入[^。；;\n]{0,30}杯(?:子)?(?:中|里)?", final_step)
    if cup is None:
        return False
    tail = final_step[cup.end():]
    if re.search(r"备用|蒸|煮|炖|炒|烤|煎|炸|焖|凝固|成型|定型|结冻|装盘|入盘", tail):
        return False
    return bool(re.search(r"即可(?:食用|享用|饮用)", tail))


def _finished_milk_drink(name: str, steps: str) -> bool:
    """A named milk drink still needs final pouring, rather than later setting."""
    if not name.endswith("撞奶") or name.endswith("姜撞奶"):
        return False
    final_step = re.split(r"第\s*\d+\s*步\s*[:：]", steps)[-1]
    actions = list(re.finditer(r"倒入牛奶|倒入杯(?:子)?|装(?:入)?杯|装入杯子", final_step))
    if not actions:
        return False
    tail = final_step[actions[-1].end():]
    return not re.search(r"凝固|成型|定型|结冻|备用|蒸|烤", tail)


def non_meal_roles(
    name: str, ingredient_names: Iterable[str], steps: str, labels: Iterable[str]
) -> list[str]:
    """Return roles supported by a finite name/ingredient/preparation rule set.

    Sweet bean soups need explicit sweetener evidence and no savory meat or
    seasoning evidence. Fruit water/juice needs a beverage name or drinking
    preparation evidence; arbitrary ``水``/``汁``/``糕`` substrings are not bans.
    """
    name = culinary_title_without_zero_soup_metadata(name)
    declared_foods = list(ingredient_names)
    foods = " ".join(declared_foods)
    steps = "".join(steps.split())
    roles: list[str] = []
    sauce_accompaniment = re.search(r"(?<!自)(?<!调)配(?!料)|佐(?!料)", name)
    # A declared cooked body served with dipping soy sauce is a finished dish,
    # while a bare sauce or a self-prepared sauce still cannot fill a meal slot.
    dipping_finished_body = (
        name.endswith("蘸酱") and name.removesuffix("蘸酱") in declared_foods
        and re.search(r"(?:蒸熟|煮熟|烤熟|炒熟|煎熟)[^。；;]{0,20}蘸酱油[^。；;]{0,12}食用", steps)
    )
    standalone_sauce = (
        name.endswith(("烧烤酱", "蘸酱", "调味酱", "花生酱", "芝麻酱"))
        and not sauce_accompaniment and not dipping_finished_body
    )
    milk_drink = _finished_milk_drink(name, steps)
    concentrate = (
        name.endswith(("蜜", "浓缩液", "果浆"))
        and "饮用" in steps
        and any(term in steps for term in ("加水", "兑水", "冲水", "泡水"))
    )
    intermediate_use = steps.rstrip("。；;!").endswith(
        ("依需求使用", "依需要使用", "用于包馅", "供后续烹饪")
    )
    if (
        any(word in name for word in ("打发", "面团", "发酵", "揉面", "测试菜"))
        or name in _PREPARATION_NAMES
        or name.endswith(("果酱", "辣椒酱", "番茄酱", "秋梨膏", "草莓酱", "蓝莓酱", "拌饭酱"))
        or concentrate
        or intermediate_use
        or standalone_sauce
        or meat_floss_component(foods.split(" "), steps)
        or slot_component_evidence(name, steps) is not None
    ):
        roles.append("component")
    animal_text = without_plant_animal_homonyms(foods)
    for egg in ("鸡蛋", "鸭蛋", "鹅蛋", "鹌鹑蛋"):
        animal_text = animal_text.replace(egg, "")
    meat = any(term in animal_text for term in _SAVORY_FOODS)
    savory = meat or any(term in foods for term in _SAVORY_SEASONINGS)
    sweet_soup = (
        name.endswith(("汤", "羹", "糊", "沙"))
        and any(base in name for base in _SWEET_SOUP_BASES)
        and any(sugar in foods for sugar in _SWEETENERS)
        and not (meat if "梨" in name else savory)
    )
    jam_filled = (
        name.endswith(("丸子", "球", "泥"))
        and any(term in foods for term in ("果酱", "蓝莓酱", "草莓酱"))
        and not savory
    )
    sweet_balls = (
        name.endswith(("丸", "丸子", "球"))
        and any(base in name for base in ("芝麻", "山药", "花生", "椰", "核桃"))
        and any(sugar in foods for sugar in _SWEETENERS)
        and not savory
    )
    molded_yam = (
        "山药" in name
        and any(sugar in foods for sugar in _SWEETENERS)
        and any(action in steps for action in ("模具", "压制成型"))
        and not savory
    )
    if not milk_drink and (
        any(
            word in name and not (word == "饼干" and crumb_coated_entree(foods.split(" "), steps))
            for word in _DESSERT_WORDS
        )
        or name.endswith(("派", "酥", "奶冻", "糖", "甜点"))
        or (name.endswith("冻") and not savory)
        or {"甜品", "甜点", "甜品风味"}.intersection(labels)
        or sweet_soup
        or jam_filled
        or sweet_balls
        or molded_yam
        or preparation_dessert_evidence(foods.split(" "), steps)
        or name.endswith("撞奶")
    ):
        roles.append("dessert")
    beverage = (
        name.endswith(("水", "汁"))
        and any(base in name for base in _BEVERAGE_BASES)
        and any(action in steps for action in ("饮用", "榨汁", "打浆", "取汁", "倒入杯"))
        and not savory
    )
    # A finite affirmative source action closes a gap in the ingredient-name
    # list (e.g. 菠菜汁). A juice substring, a cooking ingredient or a negated
    # drinking instruction alone is insufficient. Existing savoury/concentrate
    # boundaries remain; this is culinary identity, not a health judgement.
    final_clause = re.split(r"[。；;\n]", steps.rstrip("。；;!！"))[-1]
    finished_juice_drink = (
        name.endswith("汁")
        and bool(_JUICE_ACTION.search(steps))
        and bool(_DRINKING_FINISH.search(final_clause))
        and not _NEGATED_DRINKING.search(final_clause)
        and not savory
    )
    # Explicit plant-water names can be simmered by equipment, not only juiced.
    # Do not treat arbitrary 水 substrings or salt/meat broths as beverages.
    plant_water = (
        name.endswith("水")
        and any(base in name and base in foods for base in ("甘蔗", "马蹄", "茅根", "罗汉果"))
        and "水" in foods
        and not savory
    )
    processed_fruit_drink = (
        any(base in foods for base in _BEVERAGE_BASES)
        and any(action in steps for action in ("打浆", "榨汁", "取汁"))
        and any(action in steps for action in ("杯", "饮用"))
        and not savory
    )
    plum_drink = (
        "酸梅汤" in name
        and "乌梅" in foods
        and any(sugar in foods for sugar in _SWEETENERS)
        and not savory
    )
    if not concentrate and (
        name.endswith(_DRINK_SUFFIXES)
        or "果饮" in name
        or beverage
        or finished_juice_drink
        or plant_water
        or processed_fruit_drink
        or plum_drink
        or milk_drink
        or _finished_liquid_drink(
            name, steps,
            meat or any(term in foods for term in ("豆腐", "鸡蛋", "鸭蛋", "鹅蛋", "鹌鹑蛋")),
        )
    ):
        roles.append("drink")
    return roles


def is_main_meal_recipe(recipe: Recipe) -> bool:
    """Check eligibility plus source evidence, even when role metadata is stale.

    A record with no established meal role cannot silently fill a meal slot.
    The planner currently constructs ordinary meal dishes only; this does not
    introduce a dessert/beverage menu mode for breakfast or afternoon tea.
    """
    return (
        recipe.eligible
        and bool(MAIN_MEAL_ROLES.intersection(recipe.categories))
        and not NON_MEAL_ROLES.intersection(recipe.categories)
        and not (
            "soup" in recipe.categories
            and solid_soup_title_evidence(
                recipe.name, tuple(i.name for i in recipe.ingredients), recipe.steps
            ) is not None
        )
        and (
            finished_soup_evidence(recipe.name, (i.name for i in recipe.ingredients), recipe.steps)
            is None
            or recipe.categories == ["soup"]
        )
        and not preparation_only_steps(recipe.steps)
        and not grain_completion_issue(
            recipe.name, (i.name for i in recipe.ingredients), recipe.steps
        )
        and not non_meal_roles(
            recipe.name, (i.name for i in recipe.ingredients), recipe.steps, recipe.labels
        )
    )


def is_dessert_recipe(recipe: Recipe) -> bool:
    """A finished source dessert, not a drink or a main-role metadata shortcut."""
    roles = set(non_meal_roles(
        recipe.name, (i.name for i in recipe.ingredients), recipe.steps, recipe.labels,
    ))
    return (
        recipe.eligible and set(recipe.categories) == {"dessert"}
        and roles == {"dessert"} and not preparation_only_steps(recipe.steps)
        and bool(recipe.ingredients) and bool(recipe.steps.strip())
        # This first bounded allocation is for plated desserts, not a second
        # uncounted sweet soup. Liquid dessert/soup overlap needs explicit
        # quantity clarification; do not hide it behind dessert metadata.
        and not recipe.name.strip().endswith(("汤", "羹", "糊"))
        and finished_soup_evidence(recipe.name, (i.name for i in recipe.ingredients), recipe.steps) is None
        and not (
            "同烹" in recipe.name and re.search(r"[&＋+]", recipe.name)
            and len(re.findall(r"放入第[0-9一二两三四五六七八九十]+层", recipe.steps)) >= 2
        )
    )


def is_menu_recipe(recipe: Recipe, constraints) -> bool:
    """Only an explicitly allocated dessert slot extends ordinary meal roles."""
    return is_main_meal_recipe(recipe) or (
        constraints.dessert_count > 0 and is_dessert_recipe(recipe)
    )


def dessert_structure_satisfied(menu, constraints) -> bool:
    return sum(is_dessert_recipe(r) for r in menu) == constraints.dessert_count
