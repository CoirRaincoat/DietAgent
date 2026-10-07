"""Bounded source/goal repair inside verified meal and edit boundaries."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.domain.cooking_methods import main_cooking_methods
from app.domain.dining_scenes import supported_scene_preferences
from app.domain.dish_composition import composition_satisfied
from app.domain.health_evidence import HealthEvidence, no_goal_regression
from app.domain.matching_tags import flavor_coverage, scene_reference_mask
from app.domain.meal_context import meal_cost
from app.domain.method_preferences import method_swap_preserves
from app.domain.models import Constraints, Recipe
from app.domain.protein_food_references import named_protein_foods


@dataclass(frozen=True)
class HealthRepair:
    """Repaired recipes and zero-based changed slots, without a quality score."""

    recipes: list[Recipe]
    changed_indices: frozenset[int]
    diversity_tradeoff_indices: frozenset[int] = frozenset()
    source_food_tradeoff_indices: frozenset[int] = frozenset()


def source_caution_reduced(
    previous: Recipe,
    candidate: Recipe,
    goals: Sequence[str],
    evidence: Callable[[Recipe, str], HealthEvidence | None],
) -> bool:
    """Require a removed literal source caution, with no newly declared caution.

    This conservative set check cannot compare doses, brands or clinical value.
    Incidental positive foods alone cannot authorize a diversity tradeoff.
    """
    reduced = False
    for goal in dict.fromkeys(goals):
        old, new = evidence(previous, goal), evidence(candidate, goal)
        if old is None or new is None:
            continue
        before = set(
            (
                *old.discouraged_foods,
                *old.raw_cautions,
                *old.step_cautions,
                *old.bad_methods,
            )
        )
        after = set(
            (
                *new.discouraged_foods,
                *new.raw_cautions,
                *new.step_cautions,
                *new.bad_methods,
            )
        )
        if not after <= before:
            return False
        reduced |= after < before
    return reduced


def repair_health_preferences(
    menu: Sequence[Recipe],
    safe_candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    scores: Mapping[str, tuple[int, ...]],
    order: Mapping[str, int],
    food_matches: Callable[[Recipe, str], bool],
    replace_slot: int | None = None,
    allow_diversity_tradeoff: bool = False,
    goal_evidence: Callable[[Recipe, str], HealthEvidence | None] | None = None,
    protected_food_terms: Sequence[str] = (),
    source_food_reference: Callable[[Recipe], bool] | None = None,
    source_food_tradeoff: Callable[[Recipe, Recipe], bool] | None = None,
) -> HealthRepair:
    """Improve qualitative goals without summing away a conflicting goal.

    Args:
        menu: Unique provisional recipes with hard rules already checked.
        safe_candidates: Hard-screened, meal-eligible, name-deduplicated source recipes.
        constraints: Counts, ingredient preferences, meal context and edit scope.
        scores: Separate finite integer preferences, in consistent goal order.
        order: Stable retrieval order for equal gains.
        food_matches: Caller-owned ingredient preference matching.
        replace_slot: Only this one-based slot may change if supplied.
        allow_diversity_tradeoff: Permit only generic method-spread tradeoffs
            backed by strict goal gain and a removed literal source caution,
            or the explicitly authorized source-only body replacement.
        goal_evidence: Existing source evidence, required for such a tradeoff.
        protected_food_terms: Already covered query foods, in addition to the
            structured ingredient preferences; not inferred nutrition goals.
        source_food_reference: Optional finite whole-source protein-body proof.
            When provided, run only authorized body replacements, not another
            sweep of generic goal repairs or a new score axis.
        source_food_tradeoff: Optional source-authorized red-meat replacement;
            ordinary sodium-presence proxies may decrease, never doses/limits.

    Returns:
        A deterministic bounded improvement, not a globally optimal or clinically
        superior meal. Legacy swaps increase integer preference with no goal
        regression. Source-authorized swaps strictly increase the supported
        body-reference count; sodium-presence proxies for heart/BP goals may
        decrease, disclosed separately, while other goals remain protected.
        Both preserve exact culinary roles and soups,
        covered foods, explicit entree quotas, known finishing-action coverage
        and known meal fit. Unrequested method identities may still change;
        generic method diversity may decrease only under the above source gate;
        named and scoped method requirements are never waived.
        source-unknown methods are not forbidden when already unknown. A converged
        retry has no changes; unknown goals cannot force arbitrary swaps.

    Raises:
        ValueError: A nonempty, actively scored menu has a replacement slot
            outside its existing range. Empty/unscored inputs remain unchanged.
    """
    working = list(menu)
    if not working or not any(scores.get(record.recipe_id, ()) for record in working):
        return HealthRepair(working, frozenset())
    if replace_slot is not None and not 1 <= replace_slot <= len(working):
        raise ValueError("replace_slot must address an existing menu slot")
    terms = tuple(
        dict.fromkeys(
            term
            for term in [*constraints.preferred_ingredients, *protected_food_terms]
            if term.strip()
        )
    )
    masks = {
        r.recipe_id: sum(
            1 << i for i, term in enumerate(terms) if food_matches(r, term)
        )
        for r in [*working, *safe_candidates]
    }
    sources = {r.recipe_id: r for r in [*working, *safe_candidates]}
    food_reference = {
        key: int(source_food_reference(record)) if source_food_reference else 0
        for key, record in sources.items()
    }
    flavors = {
        key: flavor_coverage(r, constraints.preferences) for key, r in sources.items()
    }
    scenes = supported_scene_preferences(constraints.preferences, constraints.people)
    scene_masks = {key: scene_reference_mask(r, scenes) for key, r in sources.items()}
    cached_goal_evidence = (
        {
            goal: {key: goal_evidence(r, goal) for key, r in sources.items()}
            for goal in dict.fromkeys(constraints.health_goals)
        }
        if goal_evidence is not None and source_food_reference is None
        else {}
    )

    def coverage(records: Sequence[Recipe]) -> int:
        result = 0
        for record in records:
            result |= masks[record.recipe_id]
        return result

    # Source-only mode replaces a known red body with a supported non-red
    # body, at most once per slot. It cannot churn other roles/proxy gains.
    # Legacy mode retains the original strictly increasing integer bound.
    maximum = max((sum(vector) for vector in scores.values()), default=0)
    limit = (
        len(working)
        if source_food_reference
        else max(
            0,
            len(working) * maximum
            - sum(sum(scores.get(r.recipe_id, ())) for r in working),
        )
    )
    positions = range(len(working)) if replace_slot is None else [replace_slot - 1]
    changed: set[int] = set()
    tradeoffs: set[int] = set()
    food_tradeoffs: set[int] = set()
    health_width = len(dict.fromkeys(constraints.health_goals))
    protected_axes = [
        i
        for i, goal in enumerate(dict.fromkeys(constraints.health_goals))
        if goal not in {"降压", "护心"}
    ]
    for _ in range(limit):
        used = {r.recipe_id for r in working}
        covered = coverage(working)
        flavor_covered = 0
        for record in working:
            flavor_covered |= flavors[record.recipe_id]
        best: tuple[tuple[int, ...], int, Recipe] | None = None
        for index in positions:
            old = working[index]
            old_scores = scores.get(old.recipe_id, ())
            rest = working[:index] + working[index + 1 :]
            covered_terms: dict[str, set[str]] = {}
            if goal_evidence is not None and source_food_reference is None:
                for goal in dict.fromkeys(constraints.health_goals):
                    covered_terms[goal] = set().union(
                        *(
                            set(e.preferred_rank_terms or ())
                            for record in rest
                            if (e := cached_goal_evidence[goal][record.recipe_id])
                            is not None
                        )
                    )

            def effective_scores(record: Recipe) -> tuple[int, ...]:
                vector = list(scores.get(record.recipe_id, ()))
                if goal_evidence is not None:
                    for axis, goal in enumerate(
                        dict.fromkeys(constraints.health_goals)
                    ):
                        if axis < len(vector) and goal in covered_terms:
                            evidence = cached_goal_evidence[goal][record.recipe_id]
                            if evidence is not None:
                                vector[axis] += (
                                    evidence.score_with_covered_terms(
                                        covered_terms[goal]
                                    )
                                    - evidence.score
                                )
                return tuple(vector)

            old_scores = effective_scores(old)
            rest_coverage = coverage(rest)
            rest_flavors = 0
            for record in rest:
                rest_flavors |= flavors[record.recipe_id]
            rest_bodies = (
                frozenset().union(*(named_protein_foods(r) for r in rest))
                if source_food_reference is not None
                else frozenset()
            )
            for candidate in safe_candidates:
                new_scores = effective_scores(candidate)
                gain = sum(new_scores) - sum(old_scores)
                reference_gain = (
                    food_reference[candidate.recipe_id] - food_reference[old.recipe_id]
                )
                food_tradeoff_allowed = (
                    reference_gain > 0
                    and source_food_tradeoff is not None
                    and source_food_tradeoff(old, candidate)
                    and len(new_scores) == len(old_scores)
                    and len(new_scores) >= health_width
                    and no_goal_regression(
                        tuple(new_scores[i] for i in protected_axes)
                        + new_scores[health_width:],
                        tuple(old_scores[i] for i in protected_axes)
                        + old_scores[health_width:],
                    )
                    and (rest_flavors | flavors[candidate.recipe_id]) & flavor_covered
                    == flavor_covered
                    and scene_masks[candidate.recipe_id] & scene_masks[old.recipe_id]
                    == scene_masks[old.recipe_id]
                )
                improves_health = (
                    health_width > 0
                    and len(new_scores) >= health_width
                    and len(old_scores) >= health_width
                    and no_goal_regression(
                        new_scores[:health_width], old_scores[:health_width]
                    )
                    and sum(new_scores[:health_width]) > sum(old_scores[:health_width])
                )
                tradeoff_allowed = allow_diversity_tradeoff and (
                    food_tradeoff_allowed
                    or (
                        improves_health
                        and goal_evidence is not None
                        and source_caution_reduced(
                            old, candidate, constraints.health_goals, goal_evidence
                        )
                    )
                )
                if (
                    candidate.recipe_id in used
                    or source_food_reference is not None
                    and not food_tradeoff_allowed
                    or reference_gain < 0
                    or gain <= 0
                    and not food_tradeoff_allowed
                    or not no_goal_regression(new_scores, old_scores)
                    and not food_tradeoff_allowed
                    or set(candidate.categories) != set(old.categories)
                    or (
                        main_cooking_methods(old)
                        and not main_cooking_methods(candidate)
                    )
                    or meal_cost(candidate, constraints.meal_type)
                    > meal_cost(old, constraints.meal_type)
                    or (rest_coverage | masks[candidate.recipe_id]) & covered != covered
                    or not composition_satisfied(
                        [*rest, candidate], constraints, previous=working
                    )
                    or not method_swap_preserves(
                        working,
                        index,
                        candidate,
                        constraints.preferences,
                        scoped=constraints.scoped_methods,
                        protect_diversity=not tradeoff_allowed,
                    )
                ):
                    continue
                # Reuse finite named-food evidence to avoid replacing red meat
                # with a second copy of the already selected poultry body.
                # Only the narrow source pass uses this, never legacy repairs.
                repeated_bodies = 0
                if source_food_reference is not None:
                    repeated_bodies = len(named_protein_foods(candidate) & rest_bodies)
                rank = (
                    repeated_bodies,
                    -gain,
                    int(index not in changed),
                    order.get(candidate.recipe_id, len(order)),
                    index,
                )
                if best is None or rank < best[0]:
                    best = (rank, index, candidate)
        if best is None:
            break
        _, index, candidate = best
        if (
            food_reference[candidate.recipe_id]
            > food_reference[working[index].recipe_id]
            and source_food_tradeoff is not None
            and source_food_tradeoff(working[index], candidate)
        ):
            food_tradeoffs.add(index)
        if not method_swap_preserves(
            working,
            index,
            candidate,
            constraints.preferences,
            scoped=constraints.scoped_methods,
        ):
            tradeoffs.add(index)
        working[index] = candidate
        changed.add(index)
    return HealthRepair(
        working, frozenset(changed), frozenset(tradeoffs), frozenset(food_tradeoffs)
    )
