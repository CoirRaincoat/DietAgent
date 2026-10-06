"""Conservative named whole-dish focus, separate from all declared ingredients."""

from dataclasses import dataclass

from app.domain.food_variety import food_families
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Recipe

FOCUS_VERSION = "named-source-culinary-focus-v4"
_MARKERS: dict[str, tuple[str, ...]] = {
    "pumpkin": ("南瓜",),
    "broccoli": ("西兰花", "西蓝花"),
    "winter_melon": ("冬瓜",),
    "yam": ("山药", "淮山"),
    "rice": ("粳米", "籼米", "米饭"),
    "millet": ("小米",),
    "oats": ("燕麦",),
    "wheat": ("面粉", "小麦", "馒头", "面条"),
    "chicken": ("鸡",),
    "pork": ("猪", "肘子"),
    "egg": ("鸡蛋", "蛋黄", "蒸蛋", "蛋羹", "蛋饺", "炒蛋", "温泉蛋"),
    "tofu": ("豆腐", "豆皮", "百叶", "千张"),
    "cabbage": ("白菜", "娃娃菜"),
    "spinach": ("菠菜",),
    "tomato": ("番茄", "西红柿"),
    "carrot": ("胡萝卜",),
    "potato": ("土豆", "马铃薯"),
    "sweet_potato": ("红薯", "紫薯", "番薯"),
}
_PROTEINS = frozenset({"chicken", "pork", "egg", "tofu"})
_VEGETABLES = frozenset(
    {
        "pumpkin",
        "broccoli",
        "winter_melon",
        "yam",
        "cabbage",
        "spinach",
        "tomato",
        "carrot",
        "potato",
        "sweet_potato",
    }
)
_GRAINS = frozenset({"rice", "millet", "oats", "wheat"})
_ROOT_BASES = frozenset({"potato", "sweet_potato"})
_GRAIN_FORMS = (
    "米饭",
    "炒饭",
    "焖饭",
    "粥",
    "馒头",
    "面条",
    "饺子",
    "包子",
    "饭团",
    "花卷",
    "发糕",
    "面包",
)


@dataclass(frozen=True)
class CulinaryFocus:
    """Finite named-body families and evidence status, never nutrient dominance.

    Attributes:
        families: Established focus families; empty prevents novelty credit.
        reason: Evidence status for explaining unsupported titles/forms/roles.
    """

    families: frozenset[str]
    reason: str


def culinary_food_focus(recipe: Recipe) -> CulinaryFocus:
    """Distinguish explicit dish identity from incidental declared ingredients.

    Args:
        recipe: Original name, declared ingredients, preparation and one role.

    Returns:
        Role-compatible, source-declared families named by the dish. Grain
        forms additionally retain their declared grain bases. A grain-mixed
        cake without an established grain form, unnamed focus, unknown meat,
        ineligible source or ambiguous role remains unsupported. This cannot
        establish ingredient amounts, servings or universal title accuracy.
        Excluded garnishes remain in the original source, hard restrictions
        and nutrition evidence; absence of a focus family is not food absence.
    """
    if not is_main_meal_recipe(recipe) or len(recipe.categories) != 1:
        return CulinaryFocus(frozenset(), "meal_role_not_established")
    declared = food_families(recipe)
    name = "".join(recipe.name.split())
    named = frozenset(
        family for family in declared if any(marker in name for marker in _MARKERS[family])
    )
    role = recipe.categories[0]
    families = frozenset[str]()
    if role == "vegetable":
        families = named & _VEGETABLES
    elif role == "protein":
        # Meat of an unspecified animal must not vanish into an egg-only focus
        # or be guessed to be pork merely because a title contains 咸肉/肉末.
        if "肉" in name and not declared & {"pork", "chicken"}:
            return CulinaryFocus(frozenset(), "named_meat_source_unresolved")
        families = named & _PROTEINS
    elif role == "staple":
        # A declared grain dish may end in 菜饭/燕麦饭 rather than the literal
        # 米饭. This is a form clue only after source eligibility and exclusive
        # staple role; 下饭 is an adjective, not proof of a grain body.
        grain_form = any(form in name for form in _GRAIN_FORMS) or (
            name.endswith("饭") and not name.endswith("下饭")
        )
        if declared & _GRAINS and grain_form:
            families = (declared & _GRAINS) | (named & _VEGETABLES)
        else:
            families = named & _ROOT_BASES
    elif role == "soup":
        families = named
    return CulinaryFocus(
        families, "established_source_focus" if families else "named_body_not_established"
    )
