"""Distinguish a locally generated proposal from an original CSV record."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.models import Recipe

GENERATOR_FLAG = "generated_local_tofu_v1"
PROVIDER_FLAG = "generated_provider_tofu_v1"
LIANGFEN_FLAG = "generated_local_non_spicy_liangfen_v1"


def is_generated_recipe(recipe: "Recipe") -> bool:
    return bool({GENERATOR_FLAG, PROVIDER_FLAG, LIANGFEN_FLAG} & set(recipe.quality_flags)) and recipe.source_row == 0


def generator_version(recipe: "Recipe") -> str | None:
    if not is_generated_recipe(recipe):
        return None
    if LIANGFEN_FLAG in recipe.quality_flags:
        return "local-non-spicy-liangfen-v1"
    if "generated_local_pan_fried_tofu_v1" in recipe.quality_flags:
        return "local-pan-fried-tofu-v1"
    return "provider-tofu-proposal-v1" if PROVIDER_FLAG in recipe.quality_flags else "local-tofu-main-v1"


def display_source_row(recipe: "Recipe") -> int | None:
    # Zero is an internal non-CSV sentinel, never a fabricated source row.
    return None if is_generated_recipe(recipe) else recipe.source_row
