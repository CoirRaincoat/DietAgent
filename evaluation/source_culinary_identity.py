"""Opt-in source identity, separate from legacy replay and nutrition evidence.

Finite shared whole-food declarations repair an observation gap. They do not
establish portions, clinical suitability or that a garnish is a main dish.
Serving code and the shared_v2 history observer remain unchanged.
"""

import re
from typing import Literal

from app.domain.cooking_methods import main_cooking_methods
from app.domain.culinary_focus import CulinaryFocus, culinary_food_focus
from app.domain.entree_preferences import meat_entree_reference
from app.domain.food_names import without_plant_animal_homonyms
from app.domain.food_variety import food_families
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Recipe
from app.domain.protein_food_names import (
    ANIMAL_PROTEIN_FOODS,
    PROTEIN_FOOD_SPEC,
    declared_protein_foods,
)
from evaluation.animal_source_uncertainty import unspecified_animal_declarations

FoodIdentityPolicy = Literal["legacy", "shared_source_v1"]
VERSION = "offline-shared-source-culinary-identity-v1"
_FAMILIES = {
    "鸡蛋": "egg",
    "鱼": "fish",
    "虾": "shrimp",
    "鸡肉": "chicken",
    "猪肉": "pork",
    "牛肉": "beef",
    "鸭肉": "duck",
    "豆腐": "tofu",
}
_PORK_BODY = re.compile(r"肉(?:丸|圆|饼|片|条|卷|串|块)|肉$")
_GARNISH_PREFIX = re.compile(r"^(?:蛋黄|蛋液|虾皮|虾酱|虾粉|鱼露|鱼汤|鸡汤|猪油|鸡油|鸭油)")
_FLAVOR_WORDS = (
    "鱼香",
    "鱼露",
    "鱼汤",
    "鸡汤",
    "鸡精",
    "鸡粉",
    "蛋黄酱",
    "虾酱",
    "虾粉",
    "虾皮",
)
_EGG_MARKERS = ("鸡蛋", "鸡子", "蒸蛋", "蛋羹", "蛋饺", "炒蛋", "温泉蛋")
_TOFU_MARKERS = ("豆腐", "豆皮", "百叶", "千张")


def _declared(recipe: Recipe) -> frozenset[str]:
    shared = frozenset(
        food
        for ingredient in recipe.ingredients
        for food in declared_protein_foods(ingredient.name)
    )
    # Existing whole aliases (e.g. 猪肉馅 / 豆腐皮) are also finite source
    # declarations, not unknown merely because the shared table lacks them.
    legacy = food_families(recipe)
    return shared | frozenset(food for food, family in _FAMILIES.items() if family in legacy)


def identity_food_families(recipe: Recipe, policy: FoodIdentityPolicy = "legacy") -> frozenset[str]:
    """All finite whole declarations, including garnishes; never from titles."""
    if policy == "legacy":
        return food_families(recipe)
    if policy != "shared_source_v1":
        raise ValueError("unknown food_identity_policy")
    return food_families(recipe) | frozenset(_FAMILIES[food] for food in _declared(recipe))


def identity_culinary_focus(recipe: Recipe, policy: FoodIdentityPolicy = "legacy") -> CulinaryFocus:
    """Named, declared protein body; non-protein roles preserve legacy semantics.

    Specific animal names need a whole parsed declaration AND the existing
    meat-main reference. Finite 肉丸/肉片 bodies may refer to pork only when
    pork is the sole declared animal and no unspecified animal remains.
    Existing whole aliases such as 猪肉馅 are retained; unknown 肉末 is not pork.
    A 配 suffix, plant homonym, flavor/stock and minced garnish cannot provide
    an animal-body identity. No safety gate or recipe is rewritten.
    """
    if policy == "legacy":
        return culinary_food_focus(recipe)
    if policy != "shared_source_v1":
        raise ValueError("unknown food_identity_policy")
    if not is_main_meal_recipe(recipe) or len(recipe.categories) != 1:
        return CulinaryFocus(frozenset(), "meal_role_not_established")
    if recipe.categories != ["protein"]:
        return culinary_food_focus(recipe)
    if not main_cooking_methods(recipe):
        return CulinaryFocus(frozenset(), "source_preparation_not_established")
    declared = _declared(recipe)
    title = without_plant_animal_homonyms("".join(recipe.name.split())).split("配", 1)[0]
    for word in _FLAVOR_WORDS:
        title = title.replace(word, "")
    title = _GARNISH_PREFIX.sub("", title)
    animals = declared & ANIMAL_PROTEIN_FOODS
    named_animals = {
        food for food in animals if any(marker in title for marker in PROTEIN_FOOD_SPEC[food][0])
    }
    # Broad 肉 is not an explicit pork marker (e.g. 牛肉 with pork garnish).
    if "猪肉" in named_animals and not any(
        marker in title for marker in ("猪", "肘", "里脊", "蹄", "排骨")
    ):
        named_animals.remove("猪肉")
    pork_body = (
        animals == {"猪肉"}
        and _PORK_BODY.search(title) is not None
        and not unspecified_animal_declarations(recipe)
    )
    if pork_body:
        named_animals.add("猪肉")
    if "肉" in title and not animals:
        return CulinaryFocus(frozenset(), "named_meat_source_unresolved")
    body = (
        {_FAMILIES[food] for food in named_animals}
        if meat_entree_reference(recipe) or pork_body
        else set()
    )
    families = identity_food_families(recipe, policy)
    if "egg" in families and any(marker in title for marker in _EGG_MARKERS):
        body.add("egg")
    if "tofu" in families and any(marker in title for marker in _TOFU_MARKERS):
        body.add("tofu")
    return CulinaryFocus(
        frozenset(body), "established_shared_source_focus" if body else "named_body_not_established"
    )
