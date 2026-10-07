"""Finite animal-identity gaps in declarations, not allergy or food inference."""

import re
from functools import lru_cache

from app.domain.models import Recipe

ANIMAL_DECLARATION_POLICY = "offline-unspecified-animal-declaration-v1"
_UNSPECIFIED = re.compile(
    r"(?:生|熟|鲜)?(?:肉|瘦肉|肥肉|咸肉|肉末|瘦肉末|肉馅|肉片|肉丝|肉皮|里脊肉|排骨)"
)


@lru_cache(maxsize=8192)
def _uncertainties(names: tuple[str, ...]) -> frozenset[str]:
    return frozenset(name for name in names if _UNSPECIFIED.fullmatch(name))


def unspecified_animal_declarations(recipe: Recipe) -> frozenset[str]:
    """Report exact finite declarations lacking an explicit animal identity.

    Other declared chicken/egg/plant foods do not identify the separate 肉末.
    Neither titles nor steps resolve this declaration-only observation. It
    does not mean that no fuller source evidence could ever resolve it, or
    that all commercial/composite ingredients have been inspected. No animal,
    nutrient, dominant-body or hard-allergy alias is manufactured.
    """
    return _uncertainties(tuple(item.name.strip() for item in recipe.ingredients))
