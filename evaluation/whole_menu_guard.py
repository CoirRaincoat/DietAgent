"""Offline whole-menu evidence gates, independent of a solver's expressions.

These observations are finite engineering references, not nutrition targets.
No serving module imports this experimental menu problem.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.agent.menu_balance import balance_rank
from app.domain.cooking_methods import main_cooking_methods
from app.domain.dish_composition import composition_satisfied, dish_kind, non_meat_source_kind
from app.domain.entree_preferences import mixed_entree_active, mixed_entree_coverage
from app.domain.matching_tags import flavor_strength, scene_reference_mask
from app.domain.meal_context import meal_cost
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints, Recipe
from app.domain.scoped_methods import missing_scoped_methods, scoped_method_coverage
from app.retrieval.keyword import recipe_relevance_score
from app.rules.engine import RuleEngine, compact, contains_term
from evaluation.canonical_preference_guard import (
    CanonicalPreferences,
    canonical_preferences,
    observe_preferences,
)
from evaluation.declared_food_features import observation_food_families
from evaluation.history_food_exposure import HistoricalFoodExposure, history_food_exposure
from evaluation.method_guard_policy import MethodGuardPolicy, method_limits
from evaluation.name_evidence_guard import name_evidence_loss
from evaluation.pair_variety_probe import caution_labels
from evaluation.source_culinary_identity import (
    FoodIdentityPolicy,
    identity_culinary_focus,
    identity_food_families,
)
from evaluation.source_reference_coverage import positive_reference_labels


@dataclass(frozen=True)
class MenuFeatures:
    methods: frozenset[str]
    focus: frozenset[str]
    declared: frozenset[str]
    goals: tuple[int, ...]
    score: float
    relevance: float
    cost: int
    cautions: frozenset[str]
    references: frozenset[str]
    foods: frozenset[str]
    queries: frozenset[str]
    flavors: dict[str, int]
    scenes: int
    kind: str
    soup_kind: str
    mixed: int
    observation: frozenset[str]
    identity: HistoricalFoodExposure


@dataclass
class MenuProblem:
    seed: list[Recipe]
    records: dict[str, Recipe]
    features: dict[str, MenuFeatures]
    peers: list[list[str]]
    constraints: Constraints
    requests: CanonicalPreferences
    rules: RuleEngine
    query_terms: tuple[str, ...]
    replace_slot: int | None
    method_guard_policy: MethodGuardPolicy = "strict_baseline"
    food_identity_policy: FoodIdentityPolicy = "legacy"


def features_for(
    recipe: Recipe,
    constraints: Constraints,
    requests: CanonicalPreferences,
    rules: RuleEngine,
    queries: Sequence[str],
    food_identity_policy: FoodIdentityPolicy = "legacy",
) -> MenuFeatures:
    # Preference bonuses are protected as whole-menu source coverage, not
    # arbitrary per-slot prose. Query relevance stays per slot and queries
    # already covered in the source meal remain covered across that meal.
    relevance_constraints = constraints.model_copy(
        update={"preferences": [], "preferred_ingredients": []}
    )
    return MenuFeatures(
        frozenset(main_cooking_methods(recipe)),
        identity_culinary_focus(recipe, food_identity_policy).families,
        identity_food_families(recipe, food_identity_policy),
        rules.soft_goal_scores(recipe, constraints),
        rules.evaluate(recipe, constraints).score,
        recipe_relevance_score(recipe, queries, relevance_constraints, rules),
        meal_cost(recipe, constraints.meal_type),
        caution_labels(recipe, constraints, rules),
        positive_reference_labels(recipe, constraints, rules),
        frozenset(t for t in constraints.preferred_ingredients if rules.food_matches(recipe, t)),
        frozenset(
            t
            for t in queries
            if rules.food_matches(recipe, t)
            or any(contains_term(recipe.name, alias) for alias in rules.aliases_for(t))
            or any(contains_term(label, t) for label in recipe.labels)
        ),
        {flavor: flavor_strength(recipe, flavor) for flavor in requests.flavors},
        scene_reference_mask(recipe, requests.scenes),
        dish_kind(recipe, constraints),
        non_meat_source_kind(recipe),
        mixed_entree_coverage([recipe]),
        observation_food_families(recipe, feature_policy="shared_v2"),
        history_food_exposure(recipe, [], feature_policy="shared_v2"),
    )


def prepare_menu_problem(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    constraints: Constraints,
    rules: RuleEngine,
    *,
    query_terms: Sequence[str] = (),
    replace_slot: int | None = None,
    method_guard_policy: MethodGuardPolicy = "strict_baseline",
    food_identity_policy: FoodIdentityPolicy = "legacy",
) -> tuple[MenuProblem | None, str | None]:
    if method_guard_policy not in {"strict_baseline", "capped_balance"}:
        raise ValueError("unknown method_guard_policy")
    if food_identity_policy not in {"legacy", "shared_source_v1"}:
        raise ValueError("unknown food_identity_policy")
    if replace_slot is not None and not 1 <= replace_slot <= len(menu):
        raise ValueError("replace_slot must address an existing menu slot")
    requests = canonical_preferences(constraints)
    if requests is None or constraints.max_minutes is not None or constraints.method_meal_priority:
        return None, "unsupported_request_kept_unchanged"
    if (
        not menu
        or len(menu) != constraints.dish_count
        or len({r.recipe_id for r in menu}) != len(menu)
        or len({compact(r.name) for r in menu}) != len(menu)
        or sum("soup" in r.categories for r in menu) != constraints.soup_count
        or not composition_satisfied(menu, constraints)
        or rules.unresolved_allergies(constraints)
        or missing_scoped_methods(menu, constraints.scoped_methods, required_only=True)
        or any(
            not is_main_meal_recipe(r) or not rules.evaluate(r, constraints).allowed for r in menu
        )
    ):
        return None, "invalid_or_unresolved_seed_kept_unchanged"
    records: dict[str, Recipe] = {}
    names: set[str] = set()
    for recipe in [*menu, *candidates]:
        if recipe.recipe_id in records or compact(recipe.name) in names:
            continue
        if is_main_meal_recipe(recipe) and rules.evaluate(recipe, constraints).allowed:
            records[recipe.recipe_id] = recipe.model_copy(deep=True)
            names.add(compact(recipe.name))
    seed = [records[r.recipe_id] for r in menu]
    features = {
        key: features_for(r, constraints, requests, rules, query_terms, food_identity_policy)
        for key, r in records.items()
    }
    peers: list[list[str]] = []
    for index, old in enumerate(seed):
        before = features[old.recipe_id]
        values = [old.recipe_id]
        if replace_slot is None or index == replace_slot - 1:
            for key, recipe in records.items():
                after = features[key]
                if key == old.recipe_id:
                    continue
                if (
                    set(recipe.categories) == set(old.categories)
                    and before.focus
                    and before.declared
                    and after.focus
                    and after.declared
                    and after.cost <= before.cost
                    and after.relevance >= before.relevance
                    and name_evidence_loss(
                        before.identity,
                        after.identity,
                        before.observation,
                        after.observation,
                        frozenset(old.categories),
                    )
                    is None
                ):
                    values.append(key)
        peers.append(values)
    return (
        MenuProblem(
            seed,
            records,
            features,
            peers,
            constraints.model_copy(deep=True),
            requests,
            rules,
            tuple(query_terms),
            replace_slot,
            method_guard_policy,
            food_identity_policy,
        ),
        None,
    )


def count_features(
    menu: Sequence[Recipe], features: dict[str, MenuFeatures], field: str
) -> Counter[str]:
    if field not in {"methods", "focus", "declared", "cautions", "references", "foods", "queries"}:
        raise ValueError("unsupported counted feature")
    return Counter(label for r in menu for label in getattr(features[r.recipe_id], field))


def repeated_pairs(counts: Counter[str]) -> int:
    return sum(count * (count - 1) // 2 for count in counts.values())


def goal_sums(menu: Sequence[Recipe], features: dict[str, MenuFeatures]) -> tuple[int, ...]:
    if not menu:
        return ()
    width = len(features[menu[0].recipe_id].goals)
    return tuple(sum(features[r.recipe_id].goals[i] for r in menu) for i in range(width))


def validate_menu_proposal(problem: MenuProblem, proposal: Sequence[Recipe]) -> list[str]:
    """Recheck actual records, not solver status, hint or claimed objective.

    First-return shape guards avoid indexing invalid menus; subsequent reasons
    are independent invariants, not a minimal conflict set or quality labels.
    """
    seed, constraints, rules = problem.seed, problem.constraints, problem.rules
    if len(proposal) != len(seed):
        return ["dish_count"]
    if len({r.recipe_id for r in proposal}) != len(proposal) or len(
        {compact(r.name) for r in proposal}
    ) != len(proposal):
        return ["duplicate_identity_or_name"]
    if any(
        r.recipe_id not in problem.records
        or r.model_dump() != problem.records[r.recipe_id].model_dump()
        for r in proposal
    ):
        return ["unbound_or_modified_source"]
    if any(r.recipe_id not in problem.peers[i] for i, r in enumerate(proposal)):
        return ["slot_role_meal_query_identity_or_edit_boundary"]
    before = problem.features
    after = {
        r.recipe_id: features_for(
            r,
            constraints,
            problem.requests,
            rules,
            problem.query_terms,
            problem.food_identity_policy,
        )
        for r in proposal
    }
    failures: list[str] = []
    if any(
        not is_main_meal_recipe(r) or not rules.evaluate(r, constraints).allowed for r in proposal
    ):
        failures.append("hard_safety_or_main_meal")
    if sum("soup" in r.categories for r in proposal) != constraints.soup_count:
        failures.append("soup_count")
    if not composition_satisfied(proposal, constraints, previous=seed):
        failures.append("composition_or_mixed_reference")
    if mixed_entree_active(constraints) and mixed_entree_coverage(proposal) & mixed_entree_coverage(
        seed
    ) != mixed_entree_coverage(seed):
        failures.append("mixed_reference_types")
    if scoped_method_coverage(proposal, constraints.scoped_methods) & scoped_method_coverage(
        seed, constraints.scoped_methods
    ) != scoped_method_coverage(seed, constraints.scoped_methods):
        failures.append("scoped_methods")
    if any(a < b for a, b in zip(goal_sums(proposal, after), goal_sums(seed, before))):
        failures.append("per_goal_sum")
    if sum(after[r.recipe_id].score for r in proposal) < sum(
        before[r.recipe_id].score for r in seed
    ):
        failures.append("whole_rule_score")
    for field in ("references", "foods", "queries"):
        if not set(count_features(seed, before, field)) <= set(
            count_features(proposal, after, field)
        ):
            failures.append(field + "_coverage")
    old_cautions, new_cautions = count_features(seed, before, "cautions"), count_features(
        proposal, after, "cautions"
    )
    if any(count > old_cautions[label] for label, count in new_cautions.items()):
        failures.append("caution_frequency")
    old_preferences, new_preferences = observe_preferences(
        seed, problem.requests
    ), observe_preferences(proposal, problem.requests)
    if any(new_preferences[key] < value for key, value in old_preferences.items()):
        failures.append("canonical_preference_coverage")
    old_methods, new_methods = count_features(seed, before, "methods"), count_features(
        proposal, after, "methods"
    )
    limits = method_limits(
        old_methods,
        sum(bool(before[r.recipe_id].methods) for r in seed),
        len(seed),
        problem.method_guard_policy,
    )
    if (
        sum(bool(after[r.recipe_id].methods) for r in proposal) < limits.known_dishes
        or len(new_methods) < limits.minimum_distinct
        or any(
            count > limits.maximum_frequency.get(label, limits.new_method_frequency)
            for label, count in new_methods.items()
        )
        or repeated_pairs(new_methods) > limits.maximum_pair_repetition
        or problem.method_guard_policy == "capped_balance"
        and sum(new_methods.values()) > limits.maximum_actions
    ):
        failures.append("source_method_coverage_or_concentration")
    old_declared, new_declared = count_features(seed, before, "declared"), count_features(
        proposal, after, "declared"
    )
    if max(new_declared.values(), default=0) > max(
        old_declared.values(), default=0
    ) or repeated_pairs(new_declared) > repeated_pairs(old_declared):
        failures.append("declared_food_concentration")
    if repeated_pairs(count_features(proposal, after, "focus")) > repeated_pairs(
        count_features(seed, before, "focus")
    ):
        failures.append("culinary_focus_repetition")
    after_balance = balance_rank(proposal, len(proposal), constraints)
    before_balance = balance_rank(seed, len(seed), constraints)
    if limits.balance_prefix_width is not None:
        after_balance = after_balance[: limits.balance_prefix_width]
        before_balance = before_balance[: limits.balance_prefix_width]
    if after_balance < before_balance:
        failures.append("whole_menu_balance")
    return failures


def peer_health_frontier(problem: MenuProblem, *, witnesses_per_slot: int = 3) -> dict[str, Any]:
    """Exact additive bound inside peers; witnesses are NOT feasible meals.

    Ignore uniqueness and cross-slot guards for the bound. Higher-reference
    individual candidates retain independent rejection reasons, not clinical
    benefit or permission to recommend them.
    """
    if witnesses_per_slot < 0:
        raise ValueError("witnesses_per_slot must be nonnegative")
    width = len(dict.fromkeys(problem.constraints.health_goals))
    rows, upper = [], 0
    for slot, old in enumerate(problem.seed):
        before = problem.features[old.recipe_id]
        health = sum(before.goals[:width])
        peers = sorted(
            problem.peers[slot], key=lambda key: (-sum(problem.features[key].goals[:width]), key)
        )
        maximum = max(sum(problem.features[key].goals[:width]) for key in peers)
        upper += maximum
        witnesses = []
        for key in peers if witnesses_per_slot else []:
            after = problem.features[key]
            if sum(after.goals[:width]) <= health:
                continue
            proposed = list(problem.seed)
            proposed[slot] = problem.records[key]
            witnesses.append(
                {
                    "candidate": problem.records[key].model_dump(mode="json"),
                    "goal_sums_single_proposal": goal_sums(proposed, problem.features),
                    "source_methods": sorted(after.methods),
                    "single_proposal_violations_NOT_conflict_core": validate_menu_proposal(
                        problem, proposed
                    ),
                }
            )
            if len(witnesses) >= witnesses_per_slot:
                break
        rows.append(
            {
                "slot": slot + 1,
                "seed_id": old.recipe_id,
                "seed_name": old.name,
                "seed_health_reference_sum": health,
                "seed_methods": sorted(before.methods),
                "peer_count": len(peers),
                "peer_reference_maximum": maximum,
                "higher_reference_witnesses_NOT_recommendations": witnesses,
            }
        )
    before_sum = sum(goal_sums(problem.seed, problem.features)[:width])
    return {
        "seed_reference_sum": before_sum,
        "additive_upper_bound_ignoring_cross_slot_guards": upper,
        "strict_improvement_impossible_even_before_cross_slot_guards": upper <= before_sum,
        "warning": "Only fixed source/role/meal/query/identity peers; not entire catalog, global quality, nutrition or clinical benefit.",
        "slots": rows,
    }
