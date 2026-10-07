"""Opt-in observation features; not planner, allergy, role or health aliases.

The finite additions address declarations actually missed in local bento and
family traces. They observe foods, not their dominance, portions or safety.
Titles and preparation cannot supply missing ingredient declarations.
"""

from functools import lru_cache
from typing import Literal

from app.domain.food_variety import food_families
from app.domain.models import Recipe
from app.domain.protein_food_names import declared_protein_foods, whole_food_pattern

OBSERVATION_FAMILY_VERSION = "offline-declared-food-observation-v1"
SHARED_OBSERVATION_VERSION = "offline-shared-declared-food-observation-v2"
ObservationPolicy = Literal["observation_v1", "shared_v2"]
PROTEIN_FEATURES = frozenset(
    {"chicken", "pork", "beef", "duck", "fish", "shrimp", "yellow_croaker", "egg", "tofu"}
)
STAPLE_FEATURES = frozenset(
    {"rice", "millet", "oats", "wheat", "potato", "sweet_potato", "corn", "yam", "quinoa", "adlay"}
)
_PROTEIN_FAMILIES = {
    "鸡肉": "chicken",
    "猪肉": "pork",
    "牛肉": "beef",
    "鸭肉": "duck",
    "鱼": "fish",
    "虾": "shrimp",
    "鸡蛋": "egg",
    "豆腐": "tofu",
}
_CROAKER = whole_food_pattern(("大黄鱼", "黄鱼", "黄花鱼"))
# Exact whole grains only, not a nutritional equivalence or recipe-body proof.
_GRAINS = {
    "rice": frozenset({"糯米", "圆糯米", "长糯米", "黑米", "糙米"}),
    "quinoa": frozenset({"藜麦"}),
    "adlay": frozenset({"薏米", "薏仁"}),
}
_DECLARATIONS: dict[str, frozenset[str]] = {
    "chinese_kale": frozenset({"芥蓝"}),
    "okra": frozenset({"秋葵"}),
    "broad_bean": frozenset({"蚕豆", "鲜蚕豆"}),
    "chive": frozenset({"韭菜"}),
    "shiitake": frozenset({"香菇", "干香菇", "水发香菇"}),
    "celery": frozenset({"芹菜", "西芹"}),
    "shrimp": frozenset({"虾仁"}),
    "yellow_croaker": frozenset({"大黄鱼"}),
    "corn": frozenset({"玉米粒", "新鲜玉米"}),
    "white_radish": frozenset({"白萝卜"}),
}


def observation_version(feature_policy: ObservationPolicy) -> str:
    if feature_policy == "observation_v1":
        return OBSERVATION_FAMILY_VERSION
    if feature_policy == "shared_v2":
        return SHARED_OBSERVATION_VERSION
    raise ValueError("Unknown observation feature policy")


@lru_cache(maxsize=8192)
def _shared_families(names: tuple[str, ...]) -> frozenset[str]:
    families: set[str] = set()
    for name in names:
        compact_name = "".join(name.split())
        for key in declared_protein_foods(name):
            # One species declaration gets one feature, not species + fish.
            families.add(
                "yellow_croaker"
                if key == "鱼" and _CROAKER.fullmatch(compact_name)
                else _PROTEIN_FAMILIES[key]
            )
        for family, declarations in _GRAINS.items():
            if compact_name in declarations:
                families.add(family)
    return frozenset(families)


def observation_food_families(
    recipe: Recipe, *, feature_policy: ObservationPolicy = "observation_v1"
) -> frozenset[str]:
    """Keep planner features and add only exact finite declaration evidence.

    No substring matching, inferred animal for 肉末, title-derived ingredient,
    extract, sauce, oil or starch alias is added. Unknown declarations stay in
    the source; even a nonempty result does not mean complete food coverage.
    This opt-in evaluator must not be used as a hard-constraint normalizer.
    """
    observation_version(feature_policy)
    names = frozenset(item.name.strip() for item in recipe.ingredients)
    legacy = food_families(recipe) | frozenset(
        family for family, declarations in _DECLARATIONS.items() if names & declarations
    )
    # Preserve exact v1 replay. The new policy remains explicitly opt-in;
    # serving diversity and hard constraints never consume these features.
    if feature_policy == "observation_v1":
        return legacy
    return legacy | _shared_families(tuple(sorted(names)))
