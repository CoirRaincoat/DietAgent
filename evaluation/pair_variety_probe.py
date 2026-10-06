"""Offline bounded pair-exchange experiment; never used by the serving planner.

The default experiment protects configured whole-menu goal sums. The separate
opt-in reference-type ablation protects positive type coverage instead of
additive scores; it does not claim clinical equivalence. Source caution labels
are protected separately in either mode, never paid for with positive points.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import combinations, product
from typing import Literal

from app.agent.menu_balance import balance_rank
from app.domain.cooking_methods import cooking_method_evidence
from app.domain.culinary_focus import culinary_food_focus
from app.domain.dish_composition import composition_satisfied
from app.domain.food_variety import food_families
from app.domain.meal_context import meal_cost
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints, Recipe
from app.domain.scoped_methods import missing_scoped_methods, scoped_method_coverage
from app.retrieval.keyword import recipe_relevance_score
from app.rules.engine import RuleEngine, compact
from evaluation.canonical_preference_guard import canonical_preferences, observe_preferences
from evaluation.goal_tie_frontier import GoalTieFrontier
from evaluation.source_reference_coverage import positive_reference_labels


@dataclass
class PairProbe:
    """Observed search, not an optimality, feasibility or quality certificate."""

    recipes: list[Recipe]
    status: str
    evaluated: int = 0
    truncated_pools: int = 0
    blockers: Counter[str] = field(default_factory=Counter)
    exchanges: list[dict[str, object]] = field(default_factory=list)
    local_edit_changed: bool | None = None
    disclosures: list[str] = field(default_factory=list)


def caution_labels(recipe: Recipe, constraints: Constraints, rules: RuleEngine) -> frozenset[str]:
    """Keep goal and declared caution token, independent of its text location."""
    result: set[str] = set()
    for goal in dict.fromkeys(constraints.health_goals):
        evidence = rules.goal_evidence(recipe, goal)
        if evidence is not None:
            result.update(
                f"{goal}:{term}"
                for term in (
                    *evidence.discouraged_foods,
                    *evidence.raw_cautions,
                    *evidence.step_cautions,
                    *evidence.bad_methods,
                )
            )
    return frozenset(result)


def probe_pair_variety(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    constraints: Constraints,
    rules: RuleEngine,
    *,
    replace_slot: int | None = None,
    pool_limit: int = 32,
    evaluation_limit: int = 100_000,
    max_changed_slots: Literal[1, 2] = 2,
    goal_policy: Literal[
        "additive", "source_reference_types", "additive_and_source_reference_types"
    ] = "additive",
    preferred_food_policy: Literal["per_slot_relevance", "menu_coverage"] = "per_slot_relevance",
    preference_policy: Literal["refuse", "canonical_source_coverage"] = "refuse",
    tie_policy: Literal["stable", "source_goal_pareto"] = "stable",
    objective: Literal["named_variety", "health_reference"] = "named_variety",
) -> PairProbe:
    """Try one-/two-slot exchanges with explicit bounded-search observations.

    No service import or default flag exists. Preferences are refused by
    default. The explicit canonical_source_coverage ablation supports ONLY
    whole positive canonical items, retaining whole-menu source strength/type
    for each requested flavor, scene and finishing method. Their per-slot
    relevance bonuses are omitted only in this ablation; all other gates keep
    the original constraints. Unknown/mixed/negative clauses, time demands
    and method/meal tradeoffs stay refused. It only changes final valid menus.
    Local requests may change one slot, never gain permission for a second.
    Same-role peers retain meal fit and lexical relevance at each slot. The
    opt-in menu_coverage policy omits conversation-level preferred ingredient
    bonuses from ONLY this per-slot relevance guard, retaining every covered
    preferred ingredient across the meal. It cannot express slot-specific food
    mandates and is not an implementation of forced production replacement.
    Empty query terms here are not evidence that arbitrary query intent is
    protected. Both policies keep the original constraints for all other gates.
    In the
    default additive mode every goal's whole-menu integer sum is retained. The
    opt-in source_reference_types ablation replaces BOTH additive protections
    (goal vectors and rule-score sum) with retention of the original positive
    types for each goal; additive values may fall and must remain reported.
    In either mode caution frequencies cannot increase, and known methods/
    families cannot disappear into unknown. This is not clinical equivalence.
    The joint policy keeps BOTH additive sums and positive reference types:
    equal sums cannot hide losing the only oat reference, and retained types
    cannot excuse lower sums. Grounded food/slot method coverage is protected
    in every policy; an unmet required scoped method invalidates the input.
    The optional source_goal_pareto tie policy removes dominated configured
    goal vectors only among equal named-repeat gain and equal edit count;
    stable source order still resolves incomparable/equal vectors. Within each
    observed exchange round, no higher gain or smaller scope is traded away;
    later search paths can differ and require final-menu comparison. Default
    stable behavior is unchanged.
    Search pools and budgets are finite; no result is a proof of infeasibility.

    The health_reference ablation searches for a strictly higher whole-menu
    configured health sum, without requiring an existing named repeat. It
    requires BOTH per-goal non-regression and source-type retention, preserves
    named repetition, and reuses all existing safety/evidence/preference gates.
    It can coordinate two legal slots but cannot borrow a slot on a local edit.
    Preparation-only preference dimensions cannot create a health gain.
    This integer potential is not a quality or medical objective. No serving
    planner imports this mode; the legacy named-variety default is unchanged.
    """
    if (
        pool_limit < 1
        or evaluation_limit < 1
        or max_changed_slots not in (1, 2)
        or goal_policy
        not in ("additive", "source_reference_types", "additive_and_source_reference_types")
        or preferred_food_policy not in ("per_slot_relevance", "menu_coverage")
        or preference_policy not in ("refuse", "canonical_source_coverage")
        or tie_policy not in ("stable", "source_goal_pareto")
        or objective not in ("named_variety", "health_reference")
    ):
        raise ValueError("search limits must be positive")
    if objective == "health_reference" and goal_policy != "additive_and_source_reference_types":
        raise ValueError("health_reference requires additive_and_source_reference_types")
    working = list(menu)
    if replace_slot is not None and not 1 <= replace_slot <= len(working):
        raise ValueError("replace_slot must address an existing menu slot")
    result = PairProbe(working, "not_searched")
    if replace_slot is not None:
        result.local_edit_changed = False
        result.disclosures.append("本离线去重实验未改变指定菜位；不是生产换菜执行或全库无解证明。")
    requests = canonical_preferences(constraints)
    if (
        constraints.preferences
        and (preference_policy == "refuse" or requests is None)
        or constraints.max_minutes
        or constraints.method_meal_priority
    ):
        result.status = "unsupported_request_kept_unchanged"
        return result
    if (
        len(working) != constraints.dish_count
        or len({r.recipe_id for r in working}) != len(working)
        or len({compact(r.name) for r in working}) != len(working)
        or sum("soup" in r.categories for r in working) != constraints.soup_count
        or not composition_satisfied(working, constraints)
        or rules.unresolved_allergies(constraints)
        or missing_scoped_methods(working, constraints.scoped_methods, required_only=True)
        or any(
            not is_main_meal_recipe(r) or not rules.evaluate(r, constraints).allowed
            for r in working
        )
    ):
        result.status = "invalid_or_unresolved_input_kept_unchanged"
        return result
    unique: dict[str, Recipe] = {}
    names: set[str] = set()
    for record in [*working, *candidates]:
        if record.recipe_id in unique or compact(record.name) in names:
            continue
        if is_main_meal_recipe(record) and rules.evaluate(record, constraints).allowed:
            unique[record.recipe_id] = record
            names.add(compact(record.name))
    order = {key: i for i, key in enumerate(unique)}
    families = {k: culinary_food_focus(r).families for k, r in unique.items()}
    declared = {k: food_families(r) for k, r in unique.items()}
    methods = {k: frozenset(cooking_method_evidence(r).main_methods) for k, r in unique.items()}
    goals = {k: rules.soft_goal_scores(r, constraints) for k, r in unique.items()}
    scores = {k: rules.evaluate(r, constraints).score for k, r in unique.items()}
    relevance_constraints = (
        constraints.model_copy(update={"preferred_ingredients": []})
        if preferred_food_policy == "menu_coverage"
        else constraints
    )
    if preference_policy == "canonical_source_coverage":
        relevance_constraints = relevance_constraints.model_copy(update={"preferences": []})
    relevance = {
        k: recipe_relevance_score(r, [], relevance_constraints, rules) for k, r in unique.items()
    }
    costs = {k: meal_cost(r, constraints.meal_type) for k, r in unique.items()}
    cautions = {k: caution_labels(r, constraints, rules) for k, r in unique.items()}
    references = {k: positive_reference_labels(r, constraints, rules) for k, r in unique.items()}
    foods = {
        k: frozenset(t for t in constraints.preferred_ingredients if rules.food_matches(r, t))
        for k, r in unique.items()
    }

    def counts(records: Sequence[Recipe], mapping: dict[str, frozenset[str]]) -> Counter[str]:
        return Counter(label for r in records for label in mapping[r.recipe_id])

    def pairs(counter: Counter[str]) -> int:
        return sum(n * (n - 1) // 2 for n in counter.values())

    def vector(records: Sequence[Recipe]) -> tuple[int, ...]:
        return tuple(
            sum(goals[r.recipe_id][i] for r in records)
            for i in range(len(goals[working[0].recipe_id]))
        )

    def union(records: Sequence[Recipe]) -> set[str]:
        return {term for r in records for term in foods[r.recipe_id]}

    positions = list(range(len(working))) if replace_slot is None else [replace_slot - 1]
    potential = pairs(counts(working, families))
    health_width = len(dict.fromkeys(constraints.health_goals))
    if objective == "health_reference":
        if not any(goal in rules.config["health_goals"] for goal in constraints.health_goals):
            result.status = "no_configured_health_goal_kept_unchanged"
            return result
        # Every accepted exchange increases this integer by >= 1; the cap is
        # shared across rounds. Keep preparation dimensions outside the gain.
        maximum = max(sum(value[:health_width]) for value in goals.values())
        potential = max(0, len(working) * maximum - sum(vector(working)[:health_width]))
    result.status = "bounded_fixed_point"
    for _ in range(potential):
        before_focus = counts(working, families)
        before_declared = counts(working, declared)
        before_methods = counts(working, methods)
        before_cautions = counts(working, cautions)
        before_goals = vector(working)
        before_references = set(counts(working, references))
        before_foods = union(working)
        before_rule = sum(scores[r.recipe_id] for r in working)
        before_balance = balance_rank(working, len(working), constraints)
        before_preferences = observe_preferences(working, requests) if requests is not None else {}
        before_scoped = scoped_method_coverage(working, constraints.scoped_methods)
        used = {r.recipe_id for r in working}
        peers: dict[int, list[Recipe]] = {}
        for index in positions:
            old = working[index]
            values = (
                [
                    r
                    for k, r in unique.items()
                    if k not in used
                    and families[k]
                    and declared[k]
                    and set(r.categories) == set(old.categories)
                    and costs[k] <= costs[old.recipe_id]
                    and relevance[k] >= relevance[old.recipe_id]
                ]
                if families[old.recipe_id] and declared[old.recipe_id]
                else []
            )
            values.sort(
                key=lambda r: (
                    *(
                        (-sum(goals[r.recipe_id][:health_width]),)
                        if objective == "health_reference"
                        else ()
                    ),
                    sum(before_focus[f] for f in families[r.recipe_id]),
                    -sum(goals[r.recipe_id]),
                    order[r.recipe_id],
                )
            )
            result.truncated_pools += int(len(values) > pool_limit)
            peers[index] = [old, *values[:pool_limit]]
        scopes: list[tuple[int, ...]] = [(i,) for i in positions]
        if max_changed_slots == 2:
            scopes += list(combinations(positions, 2))
        best: tuple[tuple[int, ...], list[Recipe], tuple[int, ...]] | None = None
        frontier: GoalTieFrontier[tuple[list[Recipe], tuple[int, ...]]] = GoalTieFrontier()
        exhausted = False
        for scope in scopes:
            for replacements in product(*(peers[i] for i in scope)):
                if all(r.recipe_id == working[i].recipe_id for i, r in zip(scope, replacements)):
                    continue
                if result.evaluated >= evaluation_limit:
                    exhausted = True
                    break
                result.evaluated += 1
                proposal = list(working)
                for i, r in zip(scope, replacements):
                    proposal[i] = r
                if len({r.recipe_id for r in proposal}) != len(proposal):
                    result.blockers["duplicate_identity"] += 1
                    continue
                after_focus = counts(proposal, families)
                named_gain = pairs(before_focus) - pairs(after_focus)
                gain = (
                    sum(vector(proposal)[:health_width]) - sum(before_goals[:health_width])
                    if objective == "health_reference"
                    else named_gain
                )
                if objective == "health_reference" and named_gain < 0:
                    result.blockers["named_repeat_regression"] += 1
                    continue
                if gain <= 0:
                    result.blockers[
                        (
                            "no_health_reference_gain"
                            if objective == "health_reference"
                            else "no_named_repeat_gain"
                        )
                    ] += 1
                    continue
                if goal_policy != "source_reference_types" and any(
                    a < b for a, b in zip(vector(proposal), before_goals)
                ):
                    result.blockers["whole_menu_goal_lower"] += 1
                    continue
                if goal_policy != "additive" and not before_references <= set(
                    counts(proposal, references)
                ):
                    result.blockers["positive_source_reference_type_lost"] += 1
                    continue
                if (
                    scoped_method_coverage(proposal, constraints.scoped_methods) & before_scoped
                    != before_scoped
                ):
                    result.blockers["scoped_method_coverage_lost"] += 1
                    continue
                if any(n > before_cautions[k] for k, n in counts(proposal, cautions).items()):
                    result.blockers["declared_caution_frequency_increased"] += 1
                    continue
                if (
                    goal_policy != "source_reference_types"
                    and sum(scores[r.recipe_id] for r in proposal) < before_rule
                ):
                    result.blockers["whole_menu_rule_score_lower"] += 1
                    continue
                if not before_foods <= union(proposal) or not composition_satisfied(
                    proposal, constraints
                ):
                    result.blockers["food_coverage_or_composition_lost"] += 1
                    continue
                after_preferences = (
                    observe_preferences(proposal, requests) if requests is not None else {}
                )
                if any(after_preferences[k] < v for k, v in before_preferences.items()):
                    result.blockers["explicit_source_preference_coverage_lost"] += 1
                    continue
                after_methods = counts(proposal, methods)
                after_declared = counts(proposal, declared)
                if (
                    sum(bool(methods[r.recipe_id]) for r in proposal)
                    < sum(bool(methods[r.recipe_id]) for r in working)
                    or len(after_methods) < len(before_methods)
                    or any(n > max(1, before_methods[k]) for k, n in after_methods.items())
                    or pairs(after_methods) > pairs(before_methods)
                    or max(after_declared.values(), default=0)
                    > max(before_declared.values(), default=0)
                    or pairs(after_declared) > pairs(before_declared)
                    or balance_rank(proposal, len(proposal), constraints) < before_balance
                ):
                    result.blockers["source_method_family_or_balance_regression"] += 1
                    continue
                changed = tuple(i for i in scope if proposal[i].recipe_id != working[i].recipe_id)
                rank = (-gain, len(changed), *(order[proposal[i].recipe_id] for i in scope), *scope)
                if tie_policy == "source_goal_pareto":
                    frontier.consider(rank, vector(proposal), (proposal, changed))
                if best is None or rank < best[0]:
                    best = (rank, proposal, changed)
            if exhausted:
                break
        if tie_policy == "source_goal_pareto":
            chosen = frontier.best()
            best = (chosen[0], *chosen[1]) if chosen is not None else None
        if best is not None:
            _, proposal, changed = best
            result.exchanges.append(
                {
                    "slots": [i + 1 for i in changed],
                    "before_ids": [r.recipe_id for r in working],
                    "after_ids": [r.recipe_id for r in proposal],
                    "configured_goal_sums_before": before_goals,
                    "configured_goal_sums_after": vector(proposal),
                    "goal_policy": goal_policy,
                    "preferred_food_policy": preferred_food_policy,
                    "preference_policy": preference_policy,
                    "explicit_preferences_before": before_preferences,
                    "explicit_preferences_after": after_preferences,
                    "covered_preferred_foods_before": sorted(before_foods),
                    "covered_preferred_foods_after": sorted(union(proposal)),
                    "positive_reference_types_before": sorted(before_references),
                    "positive_reference_types_after": sorted(counts(proposal, references)),
                    "caution_counts_before": dict(before_cautions),
                    "caution_counts_after": dict(counts(proposal, cautions)),
                    "named_pairs_before": pairs(before_focus),
                    "named_pairs_after": pairs(counts(proposal, families)),
                    **({"objective": objective} if objective == "health_reference" else {}),
                    **(
                        {
                            "tie_policy": tie_policy,
                            "observed_nondominated_goal_vectors": len(frontier.values),
                        }
                        if tie_policy == "source_goal_pareto"
                        else {}
                    ),
                }
            )
            working = proposal
            result.recipes = working
        if exhausted:
            result.status = "evaluation_budget_exhausted_not_infeasible"
            break
        if best is None:
            break
    if result.truncated_pools and result.status == "bounded_fixed_point":
        result.status = "truncated_pool_fixed_point_not_infeasible"
    if replace_slot is not None:
        result.local_edit_changed = (
            result.recipes[replace_slot - 1].recipe_id != menu[replace_slot - 1].recipe_id
        )
        if result.local_edit_changed:
            result.disclosures = ["离线实验仅改变指定菜位；未运行生产换菜或真实生成回复。"]
    return result
