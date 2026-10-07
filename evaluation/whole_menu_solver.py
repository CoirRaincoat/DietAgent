"""Opt-in offline CP-SAT whole-menu experiment; never imported by serving code.

Own constraint model using the separately hashed OR-Tools dependency. A solver
status concerns this finite, conservative model, not culinary/medical quality.
Actual source records are revalidated independently before any menu is returned.
"""

import importlib
import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from time import perf_counter
from typing import Any

from app.agent.menu_balance import serving_temperature
from app.domain.entree_preferences import mixed_entree_active, mixed_entree_coverage
from app.domain.method_preferences import method_reference_mask
from app.domain.models import Constraints, Recipe
from app.domain.scoped_methods import scoped_method_coverage, scoped_method_mask
from app.rules.engine import RuleEngine
from evaluation.canonical_preference_guard import observe_preferences
from evaluation.method_guard_policy import MethodGuardPolicy, method_limits
from evaluation.source_culinary_identity import FoodIdentityPolicy
from evaluation.whole_menu_guard import (
    MenuFeatures,
    MenuProblem,
    count_features,
    goal_sums,
    prepare_menu_problem,
    repeated_pairs,
    validate_menu_proposal,
)


@dataclass
class WholeMenuResult:
    recipes: list[Recipe]
    status: str
    solver_status: str | None = None
    goal_sums_before: tuple[int, ...] = ()
    goal_sums_after: tuple[int, ...] = ()
    changed_slots: list[int] = field(default_factory=list)
    validation_failures: list[str] = field(default_factory=list)
    model_scope: dict[str, object] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    solver_seconds: float | None = None
    objective: float | None = None
    objective_bound: float | None = None


def _scaled_score(score: float) -> int:
    scaled = score * 1000
    if not math.isfinite(scaled) or abs(scaled - round(scaled)) > 1e-8:
        raise ValueError("rule score must be finite and exactly representable at scale 1000")
    return round(scaled)


def _model(
    problem: MenuProblem,
    cp: Any,
    max_changed: int | None,
    baseline: bool,
    *,
    diagnostic: bool = False,
) -> tuple[Any, dict[tuple[int, str], Any], dict[str, Any]]:
    model = cp.CpModel()
    assumptions: dict[str, Any] = {}

    def protect(expression: Any, group: str) -> None:
        constraint = model.add(expression)
        if diagnostic:
            if group not in assumptions:
                assumptions[group] = model.new_bool_var("assume_" + group)
                model.add_assumption(assumptions[group])
            constraint.only_enforce_if(assumptions[group])

    n, features = len(problem.seed), problem.features
    variables = {
        (slot, key): model.new_bool_var(f"slot_{slot}_{key}")
        for slot, peers in enumerate(problem.peers)
        for key in peers
    }

    def expression(predicate: Callable[[int, str, MenuFeatures], int]) -> Any:
        return sum(
            variable * predicate(slot, key, features[key])
            for (slot, key), variable in variables.items()
        )

    def retain(mask: int, predicate: Callable[[int, str, MenuFeatures], int]) -> None:
        for bit in range(mask.bit_length()):
            if mask & (1 << bit):
                model.add(
                    expression(lambda s, k, f: int(bool(predicate(s, k, f) & (1 << bit)))) >= 1
                )

    for slot, peers in enumerate(problem.peers):
        model.add_exactly_one(variables[slot, key] for key in peers)
        if baseline:
            model.add(variables[slot, problem.seed[slot].recipe_id] == 1)
        # Hints are not feasibility or fallback guarantees. Fixed-baseline mode
        # above verifies those actual constraints; the seed remains stored.
        for key in peers:
            model.add_hint(variables[slot, key], int(key == problem.seed[slot].recipe_id))
    for key in problem.records:
        model.add(sum(v for (s, k), v in variables.items() if k == key) <= 1)
    if max_changed is not None:
        model.add(
            sum(1 - variables[s, r.recipe_id] for s, r in enumerate(problem.seed)) <= max_changed
        )
    constraints = problem.constraints
    model.add(
        expression(lambda s, k, f: int("soup" in problem.records[k].categories))
        == constraints.soup_count
    )
    for kind, target in (
        ("meat", constraints.meat_dish_count),
        ("vegetarian", constraints.vegetarian_dish_count),
    ):
        if target is not None:
            model.add(expression(lambda s, k, f: int(f.kind == kind)) == target)
    for kind, target in (
        ("meat", constraints.meat_soup_count),
        ("vegetarian", constraints.vegetarian_soup_count),
    ):
        if target is not None:
            model.add(
                expression(
                    lambda s, k, f: int(
                        "soup" in problem.records[k].categories and f.soup_kind == kind
                    )
                )
                == target
            )
    if mixed_entree_active(constraints):
        retain(mixed_entree_coverage(problem.seed), lambda s, k, f: f.mixed)
    retain(
        scoped_method_coverage(problem.seed, constraints.scoped_methods),
        lambda s, k, f: scoped_method_mask(
            problem.records[k], constraints.scoped_methods, slot=s + 1
        ),
    )
    before_goals = goal_sums(problem.seed, features)
    for index, value in enumerate(before_goals):
        protect(expression(lambda s, k, f: f.goals[index]) >= value, "goal_dimension_" + str(index))
    protect(
        expression(lambda s, k, f: _scaled_score(f.score))
        >= sum(_scaled_score(features[r.recipe_id].score) for r in problem.seed),
        "whole_rule_score",
    )
    for field_name in ("references", "foods", "queries", "cautions"):
        old = count_features(problem.seed, features, field_name)
        labels = sorted({label for f in features.values() for label in getattr(f, field_name)})
        for label in labels:
            term = expression(lambda s, k, f: int(label in getattr(f, field_name)))
            if field_name == "cautions":
                protect(term <= old[label], "caution_frequency")
            elif old[label]:
                protect(term >= 1, field_name + "_coverage")
    preferences = observe_preferences(problem.seed, problem.requests)
    for flavor in problem.requests.flavors:
        strength = preferences["flavor:" + flavor]
        model.add(expression(lambda s, k, f: int(f.flavors[flavor] >= strength)) >= 1)
    scene_mask = sum(
        1 << i for i, scene in enumerate(problem.requests.scenes) if preferences["scene:" + scene]
    )
    retain(scene_mask, lambda s, k, f: f.scenes)
    method_mask = sum(
        1 << i
        for i, method in enumerate(problem.requests.methods)
        if preferences["method:" + method]
    )
    retain(
        method_mask,
        lambda s, k, f: method_reference_mask(
            problem.records[k], ["做法：" + m for m in problem.requests.methods]
        ),
    )

    # Finite count-to-pair tables preserve food/method concentration without
    # pretending a source's ingredient quantities or servings are known.
    method_counts = count_features(problem.seed, features, "methods")
    limits = method_limits(
        method_counts,
        sum(bool(features[r.recipe_id].methods) for r in problem.seed),
        n,
        problem.method_guard_policy,
    )
    for field_name in ("methods", "declared", "focus"):
        old = count_features(problem.seed, features, field_name)
        labels = sorted({label for f in features.values() for label in getattr(f, field_name)})
        counts, pair_terms, present_terms = [], [], []
        for label in labels:
            count = model.new_int_var(0, n, f"{field_name}_{label}_count")
            model.add(count == expression(lambda s, k, f: int(label in getattr(f, field_name))))
            pairs = model.new_int_var(0, n * (n - 1) // 2, f"{field_name}_{label}_pairs")
            model.add_element(count, [i * (i - 1) // 2 for i in range(n + 1)], pairs)
            counts.append(count)
            pair_terms.append(pairs)
            if field_name == "methods":
                present = model.new_bool_var(f"method_{label}_present")
                model.add(count >= 1).only_enforce_if(present)
                model.add(count == 0).only_enforce_if(present.negated())
                present_terms.append(present)
                protect(
                    count <= limits.maximum_frequency.get(label, limits.new_method_frequency),
                    "method_concentration",
                )
            elif field_name == "declared":
                protect(count <= max(old.values(), default=0), "declared_concentration")
        protect(
            sum(pair_terms)
            <= (limits.maximum_pair_repetition if field_name == "methods" else repeated_pairs(old)),
            field_name + "_pair_repetition",
        )
        if field_name == "methods":
            protect(sum(present_terms) >= limits.minimum_distinct, "method_distinct_count")
            protect(
                expression(lambda s, k, f: int(bool(f.methods))) >= limits.known_dishes,
                "known_method_dishes",
            )
            # Conservative coordinate-wise version of the final balance tier.
            # It may exclude lexicographically allowed improvements; disclose it.
            protect(sum(counts) <= limits.maximum_actions, "extra_method_actions")
    temperatures = Counter(serving_temperature(r) for r in problem.seed)
    for temperature in ("cold", "hot"):
        if temperatures[temperature] and (
            temperature == "cold" and n >= 4 or temperatures["hot"] and temperatures["cold"]
        ):
            model.add(
                expression(
                    lambda s, k, f: int(serving_temperature(problem.records[k]) == temperature)
                )
                >= 1
            )
    width = len(dict.fromkeys(constraints.health_goals))
    objective = expression(lambda s, k, f: sum(f.goals[:width]))
    if not baseline:
        model.add(objective >= sum(before_goals[:width]) + 1)
        if not diagnostic:
            model.maximize(objective)
    return model, variables, assumptions


def solve_whole_menu(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    constraints: Constraints,
    rules: RuleEngine,
    *,
    time_limit_seconds: float = 10,
    max_changed_slots: int | None = None,
    replace_slot: int | None = None,
    verify_baseline: bool = False,
    query_terms: Sequence[str] = (),
    method_guard_policy: MethodGuardPolicy = "strict_baseline",
    food_identity_policy: FoodIdentityPolicy = "legacy",
) -> WholeMenuResult:
    """Health-reference improvement under explicit full/local edit authority.

    Budget covers preparation, model construction and solve, with checks between
    phases (not preemption of Python feature extraction). UNKNOWN is not proof
    of infeasibility. Returned recipes are deep copies and never rewritten.
    """
    if not math.isfinite(time_limit_seconds) or time_limit_seconds <= 0:
        raise ValueError("time_limit_seconds must be finite and positive")
    if max_changed_slots is not None and (
        not isinstance(max_changed_slots, int) or max_changed_slots < 0
    ):
        raise ValueError("max_changed_slots must be a nonnegative integer")
    started = perf_counter()
    result = WholeMenuResult(
        [r.model_copy(deep=True) for r in menu],
        "time_budget_exhausted_kept_seed",
        model_scope={
            "replace_slot": replace_slot,
            "max_changed_slots": max_changed_slots,
            "verify_baseline": verify_baseline,
            "full_peer_pool": True,
            "conservative_balance_coordinates": True,
            "quality_score": False,
            "scope": "same-role/source/meal/query/identity guarded peers",
            "method_guard_policy": method_guard_policy,
            "food_identity_policy": food_identity_policy,
        },
    )

    def finish(status: str) -> WholeMenuResult:
        result.status = status
        result.elapsed_seconds = perf_counter() - started
        return result

    problem, issue = prepare_menu_problem(
        menu,
        candidates,
        constraints,
        rules,
        query_terms=query_terms,
        replace_slot=replace_slot,
        method_guard_policy=method_guard_policy,
        food_identity_policy=food_identity_policy,
    )
    if problem is None:
        return finish(issue or "invalid_seed_kept_unchanged")
    result.goal_sums_before = result.goal_sums_after = goal_sums(problem.seed, problem.features)
    result.model_scope["peer_counts"] = [len(peers) for peers in problem.peers]
    result.model_scope["candidate_records"] = len(problem.records)
    result.model_scope["method_limits"] = asdict(
        method_limits(
            count_features(problem.seed, problem.features, "methods"),
            sum(bool(problem.features[r.recipe_id].methods) for r in problem.seed),
            len(problem.seed),
            method_guard_policy,
        )
    )
    result.model_scope["explicit_method_diversity"] = problem.requests.method_diversity
    if not verify_baseline and not any(
        goal in rules.config["health_goals"] for goal in constraints.health_goals
    ):
        return finish("no_configured_health_goal_kept_unchanged")
    if perf_counter() - started >= time_limit_seconds:
        return finish("time_budget_exhausted_kept_seed")
    cp = importlib.import_module("ortools.sat.python.cp_model")
    model, variables, _ = _model(problem, cp, max_changed_slots, verify_baseline)
    remaining = time_limit_seconds - (perf_counter() - started)
    if remaining <= 0:
        return finish("time_budget_exhausted_kept_seed")
    solver = cp.CpSolver()
    solver.parameters.max_time_in_seconds = remaining
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.solve(model)
    result.solver_status = str(status.name)
    result.solver_seconds = float(solver.wall_time)
    if status == cp.INFEASIBLE:
        return finish(
            "baseline_model_rejected_kept_seed"
            if verify_baseline
            else "no_improvement_in_guarded_model_kept_seed"
        )
    if status == cp.MODEL_INVALID:
        return finish("invalid_model_kept_seed")
    if status not in (cp.FEASIBLE, cp.OPTIMAL):
        return finish("time_budget_exhausted_kept_seed")
    proposed = [
        problem.records[next(k for k in peers if solver.value(variables[s, k]))]
        for s, peers in enumerate(problem.peers)
    ]
    result.validation_failures = validate_menu_proposal(problem, proposed)
    if result.validation_failures:
        return finish("model_solution_rejected_kept_seed")
    result.goal_sums_after = goal_sums(proposed, problem.features)
    result.recipes = [r.model_copy(deep=True) for r in proposed]
    result.changed_slots = [
        s + 1
        for s, (old, new) in enumerate(zip(problem.seed, proposed))
        if old.recipe_id != new.recipe_id
    ]
    if not verify_baseline:
        result.objective = float(solver.objective_value)
        result.objective_bound = float(solver.best_objective_bound)
    return finish("validated_seed_feasible" if verify_baseline else "validated_improvement")


def diagnose_no_improvement(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    constraints: Constraints,
    rules: RuleEngine,
    *,
    time_limit_seconds: float = 10,
    max_changed_slots: int | None = None,
    replace_slot: int | None = None,
    query_terms: Sequence[str] = (),
    method_guard_policy: MethodGuardPolicy = "strict_baseline",
    food_identity_policy: FoodIdentityPolicy = "legacy",
) -> dict[str, object]:
    """Single-worker feasibility explanation with ALL guards still asserted.

    No menu is returned and no hard/soft requirement is relaxed. A sufficient
    assumption subset is not minimal, independent causal frequency, or proof
    that the whole catalog/user request is infeasible. Structural peer/safety,
    quota, temperature, canonical/scoped requirements remain unconditional.
    """
    if not math.isfinite(time_limit_seconds) or time_limit_seconds <= 0:
        raise ValueError("time_limit_seconds must be finite and positive")
    if max_changed_slots is not None and (
        not isinstance(max_changed_slots, int) or max_changed_slots < 0
    ):
        raise ValueError("max_changed_slots must be a nonnegative integer")
    started = perf_counter()
    result: dict[str, object] = {
        "status": "time_budget_exhausted",
        "solver_status": None,
        "sufficient_assumption_subset_NOT_minimal": [],
        "guards_removed": [],
        "scope": "strict health improvement inside unchanged guarded peer domain",
        "unconditional": "source/safety/local-role/meal/query/identity/quota/temperature/canonical/scoped coverage",
        "max_changed_slots": max_changed_slots,
        "replace_slot": replace_slot,
        "method_guard_policy": method_guard_policy,
        "food_identity_policy": food_identity_policy,
    }

    def finish(status: str) -> dict[str, object]:
        result["status"] = status
        result["elapsed_seconds_NOT_TTFT"] = perf_counter() - started
        return result

    problem, issue = prepare_menu_problem(
        menu,
        candidates,
        constraints,
        rules,
        query_terms=query_terms,
        replace_slot=replace_slot,
        method_guard_policy=method_guard_policy,
        food_identity_policy=food_identity_policy,
    )
    if problem is None:
        return finish(issue or "invalid_seed")
    if not any(goal in rules.config["health_goals"] for goal in constraints.health_goals):
        return finish("no_configured_health_goal")
    if perf_counter() - started >= time_limit_seconds:
        return finish("time_budget_exhausted")
    cp = importlib.import_module("ortools.sat.python.cp_model")
    model, _, assumptions = _model(problem, cp, max_changed_slots, False, diagnostic=True)
    remaining = time_limit_seconds - (perf_counter() - started)
    if remaining <= 0:
        return finish("time_budget_exhausted")
    solver = cp.CpSolver()
    solver.parameters.max_time_in_seconds = remaining
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.solve(model)
    result["solver_status"] = str(status.name)
    result["assumption_groups"] = sorted(assumptions)
    if status == cp.INFEASIBLE:
        indexes = {int(index) for index in solver.sufficient_assumptions_for_infeasibility()}
        result["sufficient_assumption_subset_NOT_minimal"] = sorted(
            group for group, variable in assumptions.items() if variable.index in indexes
        )
        return finish("guarded_strict_improvement_infeasible")
    if status in (cp.FEASIBLE, cp.OPTIMAL):
        return finish("guarded_strict_improvement_feasible_no_menu_published")
    return finish("invalid_model" if status == cp.MODEL_INVALID else "time_budget_exhausted")
