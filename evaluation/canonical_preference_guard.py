"""Finite offline preference observations, never an arbitrary prose parser."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.dining_scenes import SCENE_TAGS, scene_request_issues, supported_scene_preferences
from app.domain.matching_tags import flavor_strength, supported_flavor_preferences
from app.domain.method_preferences import method_requests, supported_method_preferences
from app.domain.models import Constraints, Recipe


@dataclass(frozen=True)
class CanonicalPreferences:
    flavors: tuple[str, ...]
    scenes: tuple[str, ...]
    methods: tuple[str, ...]
    method_diversity: bool


def canonical_preferences(constraints: Constraints) -> CanonicalPreferences | None:
    """Accept only whole canonical positive items; a recognized substring is insufficient.

    Negatives, conflicts, questions, mixed clauses and unknowns remain refused.
    This opt-in gate does not silently turn unsupported clauses into no demand.
    """
    values = constraints.preferences
    methods = method_requests(values)
    if methods.negative or methods.negative_diversity or methods.unknown:
        return None
    if scene_request_issues(values, constraints.people):
        return None
    for value in values:
        flavors = supported_flavor_preferences([value])
        canonical_method = {"做法：" + m for m in supported_method_preferences([value])}
        if not (
            len(flavors) == 1
            and value == flavors[0]
            or value in SCENE_TAGS
            or value in canonical_method
            or value == "做法多样"
        ):
            return None
    flavors = supported_flavor_preferences(values)
    if constraints.no_spicy and any("辣" in f for f in flavors):
        return None
    return CanonicalPreferences(
        flavors,
        supported_scene_preferences(values, constraints.people),
        supported_method_preferences(values),
        methods.diversity,
    )


def observe_preferences(
    menu: Sequence[Recipe],
    requests: CanonicalPreferences,
) -> dict[str, int]:
    """Whole-meal source-reference presence/strength, not actual suitability.

    Labels remain stronger than literal title references; ingredient cues and
    cached method tags cannot manufacture flavor or finishing-action coverage.
    Existing probe method guards separately protect source method diversity.
    """
    from app.domain.matching_tags import scene_reference_mask
    from app.domain.method_preferences import method_reference_mask

    result = {
        "flavor:" + flavor: max((flavor_strength(r, flavor) for r in menu), default=0)
        for flavor in requests.flavors
    }
    scene_mask = method_mask = 0
    for recipe in menu:
        scene_mask |= scene_reference_mask(recipe, requests.scenes)
        method_mask |= method_reference_mask(recipe, ["做法：" + m for m in requests.methods])
    result.update(
        {"scene:" + s: int(bool(scene_mask & (1 << i))) for i, s in enumerate(requests.scenes)}
    )
    result.update(
        {"method:" + m: int(bool(method_mask & (1 << i))) for i, m in enumerate(requests.methods)}
    )
    return result
