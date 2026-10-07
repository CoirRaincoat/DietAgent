"""Strictly decreasing declared-family repetition inside peer suitability."""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from app.domain.dish_composition import composition_satisfied
from app.domain.food_variety import food_families
from app.domain.health_evidence import no_goal_regression
from app.domain.meal_context import meal_cost
from app.domain.method_preferences import method_swap_preserves
from app.domain.models import Constraints, Recipe


@dataclass(frozen=True)
class VarietyRepair:
    """Local recipes and changed slots; not a quality or nutrient score."""

    recipes: list[Recipe]
    changed_indices: frozenset[int]


def _evidence_spread(
    records: Sequence[Recipe], evidence: Mapping[str, frozenset[str]]
) -> tuple[int, int, int, int]:
    """Return known dishes, distinct labels, largest count and repeated pairs.

    Each recipe counts once per separately established label. Missing labels
    mean unknown, not perfect diversity; these are counts, never quality points.
    """
    counts = Counter(label for r in records for label in evidence.get(r.recipe_id, ()))
    return (
        sum(bool(evidence.get(r.recipe_id)) for r in records),
        len(counts),
        max(counts.values(), default=0),
        sum(n * (n - 1) // 2 for n in counts.values()),
    )


def _label_counts(
    records: Sequence[Recipe], evidence: Mapping[str, frozenset[str]]
) -> Counter[str]:
    return Counter(label for r in records for label in evidence.get(r.recipe_id, ()))


def repair_menu_variety(
    menu: Sequence[Recipe],
    safe_candidates: Sequence[Recipe],
    constraints: Constraints,
    *,
    goal_scores: Mapping[str, tuple[int, ...]],
    rule_scores: Mapping[str, int],
    relevance: Mapping[str, float],
    order: Mapping[str, int],
    food_matches: Callable[[Recipe, str], bool],
    replace_slot: int | None = None,
    family_evidence: Mapping[str, frozenset[str]] | None = None,
    method_evidence: Mapping[str, frozenset[str]] | None = None,
    declared_family_evidence: Mapping[str, frozenset[str]] | None = None,
) -> VarietyRepair:
    """Reduce whole-menu repeated families without sacrificing suitability.

    Args:
        menu: Provisional unique meal recipes, already checked for hard rules.
        safe_candidates: Hard-screened, eligible, name-deduplicated peers.
        constraints: Explicit roles/counts, food preferences and meal context.
        goal_scores: All finite goal vectors; absent observations cannot win.
        rule_scores: Equal rule suitability is required for each substitution.
        relevance: Query relevance cannot decrease at the addressed slot.
        order: Deterministic retrieval tie order, then the slot index.
        food_matches: Caller-owned food preference matcher, not variety aliases.
        replace_slot: Restrict mutations to this one-based slot if specified.
        family_evidence: Optional separately computed culinary focus. Missing
            entries stay unknown; they never fall back to all ingredient families.
        method_evidence: Optional source finishing-action observations. Every
            swap must retain known-dish coverage and distinct methods without
            increasing the largest method count or repeated-method pairs.
            Repetition of any individual method cannot increase either: equal
            aggregate counts cannot hide moving repetitions from boil to steam.
            Missing entries remain unknown, never inferred from legacy tags.
        declared_family_evidence: Optional all-declared-food observations,
            separate from named focus. Largest family count and repeated pairs
            cannot increase; a title change cannot hide an ingredient regression.

    Returns:
        A bounded local fixed point of integer family-pair repetition, not a
        global optimum, main-food amount or culinary-method quality proof.
        Every accepted swap reduces the whole-menu potential, preserves roles,
        quotas, covered food preferences, context and every goal individually.
        Missing family evidence never wins as apparent perfect novelty.

    Raises:
        ValueError: A nonempty menu's requested slot is outside its range.
    """
    working = list(menu)
    if replace_slot is not None and working and not 1 <= replace_slot <= len(working):
        raise ValueError("replace_slot must address an existing menu slot")
    families = {
        r.recipe_id: (
            food_families(r)
            if family_evidence is None
            else family_evidence.get(r.recipe_id, frozenset())
        )
        for r in [*safe_candidates, *working]
    }
    methods = dict(method_evidence) if method_evidence is not None else None
    declared = dict(declared_family_evidence) if declared_family_evidence is not None else None
    terms = tuple(dict.fromkeys(t for t in constraints.preferred_ingredients if t.strip()))
    masks = {
        r.recipe_id: sum(1 << i for i, term in enumerate(terms) if food_matches(r, term))
        for r in [*safe_candidates, *working]
    }

    def coverage(records: Sequence[Recipe]) -> int:
        mask = 0
        for r in records:
            mask |= masks[r.recipe_id]
        return mask

    def overlap(record: Recipe, rest: Sequence[Recipe]) -> int:
        return sum(len(families[record.recipe_id] & families[r.recipe_id]) for r in rest)

    # Count each family shared by a pair once, even when recipe aliases repeat.
    potential = sum(overlap(r, working[i + 1 :]) for i, r in enumerate(working))
    changed: set[int] = set()
    positions = range(len(working)) if replace_slot is None else [replace_slot - 1]
    for _ in range(potential):
        used = {r.recipe_id for r in working}
        covered = coverage(working)
        method_before = _evidence_spread(working, methods) if methods is not None else None
        method_counts = _label_counts(working, methods) if methods is not None else Counter()
        declared_before = _evidence_spread(working, declared) if declared is not None else None
        best: tuple[tuple[int, ...], int, Recipe] | None = None
        for index in positions:
            old = working[index]
            if not families[old.recipe_id]:
                continue
            rest = working[:index] + working[index + 1 :]
            old_overlap = overlap(old, rest)
            if old_overlap == 0:
                continue
            rest_coverage = coverage(rest)
            for candidate in safe_candidates:
                key = candidate.recipe_id
                if (
                    key in used
                    or not families[key]
                    or key not in goal_scores
                    or old.recipe_id not in goal_scores
                    or key not in rule_scores
                    or old.recipe_id not in rule_scores
                    or key not in relevance
                    or old.recipe_id not in relevance
                    or rule_scores[key] != rule_scores[old.recipe_id]
                    or not no_goal_regression(goal_scores[key], goal_scores[old.recipe_id])
                    or relevance[key] < relevance[old.recipe_id]
                    or set(candidate.categories) != set(old.categories)
                    or meal_cost(candidate, constraints.meal_type)
                    > meal_cost(old, constraints.meal_type)
                    or (rest_coverage | masks[key]) & covered != covered
                    or not composition_satisfied([*rest, candidate], constraints, previous=working)
                    or not method_swap_preserves(working, index, candidate, constraints.preferences, scoped=constraints.scoped_methods)
                ):
                    continue
                gain = old_overlap - overlap(candidate, rest)
                if gain <= 0:
                    continue
                proposal = [*rest, candidate]
                if methods is not None and method_before is not None:
                    after = _evidence_spread(proposal, methods)
                    if (
                        after[0] < method_before[0]
                        or after[1] < method_before[1]
                        or after[2] > method_before[2]
                        or after[3] > method_before[3]
                        or any(
                            n > max(1, method_counts[label])
                            for label, n in _label_counts(proposal, methods).items()
                        )
                    ):
                        continue
                if declared is not None and declared_before is not None:
                    if not declared.get(key) or not declared.get(old.recipe_id):
                        continue
                    after = _evidence_spread(proposal, declared)
                    if after[2] > declared_before[2] or after[3] > declared_before[3]:
                        continue
                rank = (-gain, int(index not in changed), order.get(key, len(order)), index)
                if best is None or rank < best[0]:
                    best = (rank, index, candidate)
        if best is None:
            break
        _, index, candidate = best
        working[index] = candidate
        changed.add(index)
    return VarietyRepair(working, frozenset(changed))
