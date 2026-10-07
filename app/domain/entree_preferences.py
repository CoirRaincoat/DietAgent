"""Finite mixed-entrée references, separate from quotas, nutrition and portions."""

import re
from collections.abc import Iterable, Sequence
from functools import lru_cache

from app.domain.dish_composition import known_no_meat_food, non_meat_source_kind
from app.domain.dish_roles import primary_dish_role
from app.domain.food_names import without_plant_animal_homonyms
from app.domain.models import Constraints, Ingredient, Recipe
from app.domain.protein_food_names import PROTEIN_FOOD_SPEC, whole_food_pattern

ENTREE_PREFERENCE_VERSION = "source-mixed-entree-reference-v2-shared-food-names"
_MENTION = re.compile(r"如果|假如|例如|比如|解释|为什么|什么叫|是否|[?？\"'“”‘’]")
_SPLIT = re.compile(r"[,，;；。.!！\r\n]+|不过|但是|可是|然而|而是|但")
_PREFIX = r"(?:(?:我|我们|这餐|本餐)?(?:想要|希望|要|注意|尽量))?"
_MIXED = r"(?:荤素(?:搭配|均衡|结合|都有|都要)|有荤有素)"
_REQUEST = re.compile(r"^" + _PREFIX + r"(?P<negative>不要|别)?" + _MIXED + r"$")
_JOINT = re.compile(r"^" + _PREFIX + r"荤素(?:搭配|均衡)?(?:和|与|及|、)做法(?:多样|丰富|多样性)$")
_MEAT_STEMS = {
    "鸡": ("鸡胸肉", "鸡腿肉", "鸡肉", "鸡翅", "鸡腿", "鸡胸", "三黄鸡", "整鸡", "鸡爪"),
    "猪": ("猪肉", "猪瘦肉", "猪肘子", "肘子", "猪里脊", "里脊肉", "猪排", "猪蹄"),
    "牛": ("牛肉", "牛腩", "牛排", "牛里脊"),
    "羊": ("羊肉", "羊排", "羊腿肉"),
    "鸭": ("鸭肉", "鸭腿", "鸭胸肉"),
    "鹅": ("鹅肉",),
    "肉": ("肉末", "肉馅", "瘦肉", "排骨", "肋排"),
    "鱼": ("鲈鱼", "草鱼", "鳕鱼", "鲳鱼", "银鲳", "鱼肉", "鱼片", "带鱼", "三文鱼", "鳝鱼"),
    "虾": ("虾仁", "鲜虾", "大虾", "虾", "青虾", "基围虾"),
    "贝": ("扇贝肉", "蛤蜊", "蛏子", "生蚝"),
}
_TITLE_MARKERS = {
    "鸡": ("鸡", "凤爪"),
    "猪": ("猪", "肉", "肘", "里脊", "蹄", "排骨"),
    "牛": ("牛",),
    "羊": ("羊",),
    "鸭": ("鸭",),
    "鹅": ("鹅",),
    "肉": ("肉", "排骨", "肋排"),
    "鱼": ("鱼", "鲈", "鲳", "三文", "鳝"),
    "虾": ("虾",),
    "贝": ("贝", "蛤", "蛏", "蚝"),
}
for _family, _food in {"鸡": "鸡肉", "猪": "猪肉", "牛": "牛肉", "鸭": "鸭肉", "鱼": "鱼", "虾": "虾"}.items():
    _markers, _stems = PROTEIN_FOOD_SPEC[_food]
    _MEAT_STEMS[_family] = tuple(dict.fromkeys((*_MEAT_STEMS[_family], *_stems)))
    _TITLE_MARKERS[_family] = tuple(dict.fromkeys((*_TITLE_MARKERS[_family], *_markers)))
_MEAT_NAMES = {
    family: whole_food_pattern(stems)
    for family, stems in _MEAT_STEMS.items()
}
_GARNISH_BODY_TITLE = re.compile(
    r"^(?:榄菜|橄榄菜|红油)?(?:肉末|肉沫|肉碎|肉糜|鸡丝|虾皮|猪油|鸡油|鸭油)"
    r"(?:炒|烧|煸|蒸|拌|炖|煮|烩)?(?P<body>.+)$"
)


def joint_entree_variety_clause(text: str) -> bool:
    """Only a complete literal conjunction, never a question or broad substring."""
    return not _MENTION.search(text) and _JOINT.fullmatch("".join(text.split())) is not None


def entree_request_clauses(text: str) -> tuple[str, ...]:
    if _MENTION.search(text):
        return ()
    result = []
    for clause in _SPLIT.split(text):
        clause = "".join(clause.split())
        if joint_entree_variety_clause(clause):
            value = "荤素搭配"
        elif match := _REQUEST.fullmatch(clause):
            value = "不要荤素搭配" if match["negative"] else "荤素搭配"
        else:
            continue
        if value not in result:
            result.append(value)
    return tuple(result)


def entree_request_state(preferences: Iterable[str]) -> tuple[bool, bool]:
    return _request_state(tuple(preferences))


@lru_cache(maxsize=1024)
def _request_state(preferences: tuple[str, ...]) -> tuple[bool, bool]:
    clauses = {c for text in preferences for c in entree_request_clauses(text)}
    return "荤素搭配" in clauses, "不要荤素搭配" in clauses


def mixed_entree_active(constraints: Constraints) -> bool:
    """Explicit mixed request or shared-main-meal default, never override diet/quota.

    Defaults concern dish references only: three-plus diners, lunch/dinner and
    five-plus non-soup slots. A zero meat/no-meat quota, negative/conflicting
    request or whole-meal vegetarian mode prevents this default from adding meat.
    """
    requested, negative = entree_request_state(constraints.preferences)
    if (
        negative
        or constraints.diet_mode != "omnivore"
        or (constraints.meat_dish_count == 0 or constraints.vegetarian_dish_count == 0)
    ):
        return False
    return requested or (
        constraints.people >= 3
        and constraints.meal_type in {"午餐", "晚餐"}
        and constraints.dish_count - constraints.soup_count >= 5
    )


@lru_cache(maxsize=8192)
def _meat_reference(
    name: str,
    foods: tuple[str, ...],
    steps: str,
    labels: str,
    roles: tuple[str, ...],
    flags: tuple[str, ...],
) -> bool:
    if "unparsed_ingredients" in flags or roles != ("protein",):
        return False
    if primary_dish_role(name, foods, steps, re.split(r"[、,，;；|\s]+", labels)) != "protein":
        return False
    garnish = _GARNISH_BODY_TITLE.fullmatch("".join(name.split()))
    if garnish is not None and (
        known_no_meat_food(garnish["body"])
        or garnish["body"] == "菜心" and "菜心" in foods
    ):
        # Complete named plant/egg/tofu body with a finite mince/flavor prefix
        # is not a named meat-main reference. Animal presence still survives
        # in source-kind/allergy gates; no vegetarian status or ratio follows.
        return False
    title = without_plant_animal_homonyms("".join(name.split()))
    return any(
        any(marker in title for marker in _TITLE_MARKERS[family])
        and any(pattern.fullmatch("".join(food.split())) for food in foods)
        for family, pattern in _MEAT_NAMES.items()
    )


def meat_entree_reference(recipe: Recipe) -> bool:
    """Named protein dish plus complete finite declared meat/fish body name.

    Eggs, meat broth, shrimp skin, animal fat, sauce and a prepared product name
    cannot stand in for this reference. No ingredient proportion is inferred;
    a positive reference is not proof of a main-sized meat serving.
    """
    return recipe.eligible and _meat_reference(
        recipe.name,
        tuple(i.name for i in recipe.ingredients),
        recipe.steps,
        recipe.raw_label,
        tuple(recipe.categories),
        tuple(recipe.quality_flags),
    )


def independent_meat_entree_reference(recipe: Recipe) -> bool:
    """A stricter finite quantity reference, not an inferred ingredient ratio.

    A minced-meat/flavoring prefix attached to a complete known no-meat body
    cannot certify an independent meat main just from its protein role. This
    does not relabel that dish vegetarian, reject all minced-meat dishes or
    reinterpret the older qualitative/animal-source convention globally.
    """
    title = "".join(recipe.name.split())
    garnish = _GARNISH_BODY_TITLE.fullmatch(title)
    if garnish is not None and known_no_meat_food(garnish["body"]):
        return False
    return meat_entree_reference(recipe)


def entree_reference_mask(recipe: Recipe) -> int:
    return _entree_reference_mask(
        recipe.name,
        tuple(i.name for i in recipe.ingredients),
        recipe.steps,
        recipe.raw_label,
        tuple(recipe.categories),
        tuple(recipe.quality_flags),
        recipe.eligible,
    )


@lru_cache(maxsize=8192)
def _entree_reference_mask(
    name: str,
    foods: tuple[str, ...],
    steps: str,
    labels: str,
    roles: tuple[str, ...],
    flags: tuple[str, ...],
    eligible: bool,
) -> int:
    # Exact facts inspected by the finite classifiers, never ID/user/portion.
    # Source, role or eligibility changes cannot reuse a stale positive bit.
    if not eligible or len(roles) != 1 or roles[0] not in {"protein", "vegetable"}:
        return 0
    if _meat_reference(name, foods, steps, labels, roles, flags):
        return 1
    role = primary_dish_role(
        name,
        foods,
        steps,
        re.split(r"[、,，;；|\s]+", labels),
    )
    source = Recipe(
        recipe_id="reference-only",
        name=name,
        raw_ingredients="、".join(foods),
        steps=steps,
        ingredients=[Ingredient(name=food, raw=food) for food in foods],
        quality_flags=list(flags),
        source_row=0,
        fingerprint="reference-only",
    )
    return 2 if role == roles[0] and non_meat_source_kind(source) == "vegetarian" else 0


def mixed_entree_coverage(menu: Sequence[Recipe]) -> int:
    result = 0
    for recipe in menu:
        result |= entree_reference_mask(recipe)
    return result


def mixed_entree_preserves(
    previous: Sequence[Recipe], proposed: Sequence[Recipe], constraints: Constraints
) -> bool:
    if not mixed_entree_active(constraints):
        return True
    before = mixed_entree_coverage(previous)
    return mixed_entree_coverage(proposed) & before == before


def mixed_entree_warnings(menu: Sequence[Recipe], constraints: Constraints) -> list[str]:
    requested, negative = entree_request_state(constraints.preferences)
    if requested and (
        negative
        or constraints.diet_mode != "omnivore"
        or constraints.meat_dish_count == 0
        or constraints.vegetarian_dish_count == 0
    ):
        return [
            "荤素搭配参考与否定要求、整餐素食模式或明确零道数量不一致；保留已确认的限制，不擅自加肉或改变数量。"
        ]
    if not mixed_entree_active(constraints):
        return []
    mask = mixed_entree_coverage(menu)
    missing = [
        name
        for bit, name in ((1, "肉鱼菜主体参考"), (2, "无肉菜来源参考（允许蛋奶）"))
        if not mask & bit
    ]
    warnings = [
        "荤素结构仅核对有限菜谱来源：蛋类/豆腐蛋白质菜位不自动等于肉菜，汤、主食、虾皮和肉汤调味不填肉鱼主体参考；未核验食材比例、每人份量或营养达标。"
    ]
    if missing:
        warnings.append(
            "本餐荤素结构参考尚缺："
            + "、".join(missing)
            + "；当前安全候选、已覆盖要求或本轮换菜范围内未补齐，不宣称已满足。"
        )
    return warnings
