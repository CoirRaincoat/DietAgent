"""Offline cross-meal ablation: repeated additive credit versus source-type coverage.

Never imported by the serving application. Reference-type retention is an
engineering experiment, not proof that less additive credit is clinically equal.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import combinations, product
from typing import Literal

from app.agent.menu_balance import balance_rank
from app.domain.cooking_methods import main_cooking_methods
from app.domain.culinary_focus import culinary_food_focus
from app.domain.dish_composition import composition_satisfied
from app.domain.food_variety import food_families
from app.domain.matching_tags import scene_reference_mask
from app.domain.meal_context import meal_cost
from app.domain.meal_history import recommendation_counts
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints, Recipe
from app.domain.scoped_methods import missing_scoped_methods, scoped_method_coverage
from app.retrieval.keyword import recipe_relevance_score
from app.rules.engine import RuleEngine, compact, contains_term
from evaluation.canonical_preference_guard import canonical_preferences, observe_preferences
from evaluation.declared_food_features import (
    ObservationPolicy,
    observation_food_families,
    observation_version,
)
from evaluation.history_food_exposure import history_food_exposure
from evaluation.name_evidence_guard import name_evidence_loss
from evaluation.pair_variety_probe import caution_labels
from evaluation.source_reference_coverage import positive_reference_labels
from evaluation.stratified_candidate_pool import bound_declared_food_pool


@dataclass
class HistoryProbe:
    recipes: list[Recipe]
    status: str
    evaluated: int = 0
    truncated_pools: int = 0
    blockers: Counter[str] = field(default_factory=Counter)
    exchanges: list[dict[str, object]] = field(default_factory=list)


def saturated_goal_scores(
    menu: Sequence[Recipe], constraints: Constraints, rules: RuleEngine
) -> tuple[int, ...]:
    """Cap duplicate positive components, not per-dish negative evidence.

    Reuse the configured evidence formula: category 2, preferred food 2,
    method 1; each component contributes once per meal. Preserve -3 per
    food-caution dish and -2 per bad-method dish. This is an offline engineering
    metric, not a nutritional target or clinical equivalence certificate.
    Separate type guards prevent losing a sole oat reference to another food.
    """
    values = []
    for goal in dict.fromkeys(constraints.health_goals):
        evidence = [e for r in menu if (e := rules.goal_evidence(r, goal)) is not None]
        values.append(
            2
            * any(
                e.category_foods and e.category_rank_enabled and e.positive_rank_enabled
                for e in evidence
            )
            + 2 * any(e.preferred_foods and e.positive_rank_enabled for e in evidence)
            + any(e.good_methods and e.positive_rank_enabled for e in evidence)
            - 3
            * sum(bool(e.discouraged_foods or e.raw_cautions or e.step_cautions) for e in evidence)
            - 2 * sum(bool(e.bad_methods) for e in evidence)
        )
    return tuple(values)


def probe_history_rotation(
    menu: Sequence[Recipe],
    candidates: Sequence[Recipe],
    constraints: Constraints,
    rules: RuleEngine,
    history: Sequence[Sequence[str]],
    *,
    policy: Literal["additive_and_types", "saturated_types"] = "additive_and_types",
    pool_limit: int = 32,
    evaluation_limit: int = 50000,
    replace_slot: int | None = None,
    query_terms: Sequence[str] = (),
    recheck_soft_preferences: bool = True,
    ranking: Literal["name_exposure", "declared_content"] = "name_exposure",
    history_menus: Sequence[Sequence[Recipe]] | None = None,
    pool_strategy: Literal["flat", "declared_food_stratified"] = "flat",
    preserve_name_evidence: bool = False,
    feature_policy: ObservationPolicy = "observation_v1",
) -> HistoryProbe:
    """Bounded one/two-slot exchanges with explicit per-goal tradeoff evidence.

    Both policies preserve unique positive types, caution frequencies, source
    role/meal/method/food/reference coverage and edit scope. Each old slot's
    requested scene references must survive in that slot; another scene or
    flavor credit elsewhere cannot pay for losing it. These are metadata
    observations, not transport/storage or suitability certification. Saturated mode may
    lower health-only additive sums; additional preparation preference dimensions
    remain protected. History records recommendations, not consumption. Search
    limits and unsupported cases cannot prove full-catalog infeasibility.
    Opt-in declared_content holds observed/missing same-role history pairs
    fixed per slot and cannot increase name exposure. It may improve content
    when names already differ; unknown-only slots stay fixed. Historical recipe
    records must correspond exactly to the supplied names, never guessed from
    a current catalog. Its finite features are not dominant-food or health facts.
    V3 content exposure also refuses pairs with finite unspecified animal
    declarations or unrepresented protein-role source features, so known
    garnish features cannot hide a missing meat identity or representation.
    Optional declared_food_stratified sampling is applied only after existing
    slot gates. It keeps the old slot and finite ingredient-signature groups,
    but can discard a useful flat-pool candidate and does not promise gain.
    Flat remains the default; neither option changes serving recommendations.
    Feature policy defaults to exact v1 replay. Shared_v2 is an opt-in declared
    protein/grain observation with an additional staple-role gap; it does not
    infer portions, health, safety or complete Chinese ingredient coverage.
    """
    observation_version(feature_policy)
    if (
        policy not in ("additive_and_types", "saturated_types")
        or ranking not in ("name_exposure", "declared_content")
        or pool_strategy not in ("flat", "declared_food_stratified")
        or min(pool_limit, evaluation_limit) < 1
    ):
        raise ValueError("unsupported policy or nonpositive search limit")
    if (ranking == "declared_content" or preserve_name_evidence) and (
        history_menus is None
        or [[compact(r.name) for r in menu] for menu in history_menus]
        != [[compact(name) for name in menu] for menu in history]
    ):
        raise ValueError("Content ranking requires matching historical recipe records")
    working = list(menu)
    result = HistoryProbe(working, "not_searched")
    if replace_slot is not None and not 1 <= replace_slot <= len(working):
        raise ValueError("replace_slot must address an existing slot")
    if replace_slot is not None:
        result.status = "local_edit_kept_unchanged"
        return result
    if not recheck_soft_preferences:
        result.status = "continuation_kept_unchanged"
        return result
    requests = canonical_preferences(constraints)
    if requests is None or constraints.max_minutes or constraints.method_meal_priority:
        result.status = "unsupported_request_kept_unchanged"
        return result
    if (
        not working
        or len(working) != constraints.dish_count
        or len({compact(r.name) for r in working}) != len(working)
        or len({r.recipe_id for r in working}) != len(working)
        or sum("soup" in r.categories for r in working) != constraints.soup_count
        or not composition_satisfied(working, constraints)
        or rules.unresolved_allergies(constraints)
        or missing_scoped_methods(working, constraints.scoped_methods, required_only=True)
        or any(
            not is_main_meal_recipe(r) or not rules.evaluate(r, constraints).allowed
            for r in working
        )
    ):
        result.status = "invalid_input_kept_unchanged"
        return result
    exposures = recommendation_counts(history)
    if not exposures:
        result.status = "no_history_kept_unchanged"
        return result
    unique: dict[str, Recipe] = {}
    names: set[str] = set()
    for r in [*working, *candidates]:
        if r.recipe_id in unique or compact(r.name) in names:
            continue
        if is_main_meal_recipe(r) and rules.evaluate(r, constraints).allowed:
            unique[r.recipe_id] = r
            names.add(compact(r.name))
    order = {key: i for i, key in enumerate(unique)}
    references = {k: positive_reference_labels(r, constraints, rules) for k, r in unique.items()}
    cautions = {k: caution_labels(r, constraints, rules) for k, r in unique.items()}
    goals = {k: rules.soft_goal_scores(r, constraints) for k, r in unique.items()}
    methods = {k: frozenset(main_cooking_methods(r)) for k, r in unique.items()}
    families = {k: food_families(r) for k, r in unique.items()}
    focus = {k: culinary_food_focus(r).families for k, r in unique.items()}
    signatures = (
        {k: observation_food_families(r, feature_policy=feature_policy) for k, r in unique.items()}
        if pool_strategy == "declared_food_stratified" or preserve_name_evidence
        else {}
    )
    foods = {
        k: frozenset(t for t in constraints.preferred_ingredients if rules.food_matches(r, t))
        for k, r in unique.items()
    }
    queries = {
        k: frozenset(
            term
            for term in query_terms
            if rules.food_matches(r, term)
            or any(contains_term(r.name, alias) for alias in rules.aliases_for(term))
            or any(contains_term(label, term) for label in r.labels)
        )
        for k, r in unique.items()
    }
    costs = {k: meal_cost(r, constraints.meal_type) for k, r in unique.items()}
    scenes = {k: scene_reference_mask(r, requests.scenes) for k, r in unique.items()}
    relevance = {
        k: recipe_relevance_score(r, query_terms, constraints, rules) for k, r in unique.items()
    }
    content = (
        {k: history_food_exposure(r, history_menus or (), feature_policy=feature_policy)
         for k, r in unique.items()}
        if ranking == "declared_content" or preserve_name_evidence
        else {}
    )
    if ranking == "declared_content" and not any(
        content[r.recipe_id].observed_pairs for r in working
    ):
        result.status = "no_observed_content_kept_unchanged"
        return result

    def counts(records: Sequence[Recipe], values: dict[str, frozenset[str]]) -> Counter[str]:
        return Counter(v for r in records for v in values[r.recipe_id])

    def pairs(values: Counter[str]) -> int:
        return sum(n * (n - 1) // 2 for n in values.values())

    def vector(records: Sequence[Recipe]) -> tuple[int, ...]:
        return tuple(
            sum(goals[r.recipe_id][i] for r in records)
            for i in range(len(goals[working[0].recipe_id]))
        )

    def exposure(records: Sequence[Recipe]) -> int:
        return sum(exposures[compact(r.name)] for r in records)

    def content_cost(records: Sequence[Recipe]) -> int:
        return sum(content[r.recipe_id].weighted_cosine_units or 0 for r in records)

    def objective(records: Sequence[Recipe]) -> tuple[int, ...]:
        return (
            (content_cost(records), exposure(records))
            if ranking == "declared_content"
            else (exposure(records),)
        )

    result.status = "bounded_fixed_point"
    health_width = len(dict.fromkeys(constraints.health_goals))
    # Exposure is a finite strictly decreasing integer potential. Evaluation
    # budget is global across accepted rounds, not renewed per exchange.
    while exposure(working) or (ranking == "declared_content" and content_cost(working)):
        pool_cautions = counts(working, cautions)
        pool_references = set(counts(working, references))
        peers: list[list[Recipe]] = []
        for old in working:
            pool = [
                r
                for r in unique.values()
                if set(r.categories) == set(old.categories)
                and costs[r.recipe_id] <= costs[old.recipe_id]
                and relevance[r.recipe_id] >= relevance[old.recipe_id]
                # A new caution label cannot be cancelled by removing another
                # dish. Apply this necessary guard before finite pool bounding.
                and set(cautions[r.recipe_id]) <= set(pool_cautions)
            ]
            retained = [
                r
                for r in pool
                if scenes[r.recipe_id] & scenes[old.recipe_id] == scenes[old.recipe_id]
            ]
            result.blockers["scene_reference_pool_rejected"] += len(pool) - len(retained)
            pool = retained
            if preserve_name_evidence:
                retained = []
                for candidate in pool:
                    loss_reason = name_evidence_loss(
                        content[old.recipe_id], content[candidate.recipe_id],
                        signatures[old.recipe_id], signatures[candidate.recipe_id],
                        frozenset(old.categories),
                    )
                    if loss_reason is None:
                        retained.append(candidate)
                    else:
                        result.blockers[f"name_evidence_{loss_reason}_pool_rejected"] += 1
                pool = retained
            if ranking == "declared_content":
                old_observation = content[old.recipe_id]
                result.blockers["content_animal_identity_pool_rejected"] += sum(
                    r.recipe_id != old.recipe_id
                    and old_observation.observed_pairs > 0
                    and content[r.recipe_id].unspecified_animal_pairs
                    > old_observation.unspecified_animal_pairs
                    for r in pool
                )
                result.blockers["content_protein_feature_pool_rejected"] += sum(
                    r.recipe_id != old.recipe_id
                    and old_observation.observed_pairs > 0
                    and content[r.recipe_id].unrepresented_protein_pairs
                    > old_observation.unrepresented_protein_pairs
                    for r in pool
                )
                result.blockers["content_staple_feature_pool_rejected"] += sum(
                    r.recipe_id != old.recipe_id
                    and old_observation.observed_pairs > 0
                    and content[r.recipe_id].unrepresented_staple_pairs
                    > old_observation.unrepresented_staple_pairs
                    for r in pool
                )
                retained = [
                    r
                    for r in pool
                    if (
                        r.recipe_id == old.recipe_id
                        or old_observation.observed_pairs > 0
                        and content[r.recipe_id].observed_pairs == old_observation.observed_pairs
                        and content[r.recipe_id].missing_pairs == old_observation.missing_pairs
                    )
                ]
                result.blockers["content_coverage_pool_rejected"] += len(pool) - len(retained)
                pool = retained
            pool.sort(
                key=lambda r: (
                    *((content_cost([r]),) if ranking == "declared_content" else ()),
                    exposures[compact(r.name)],
                    -len(references[r.recipe_id] & pool_references),
                    len(cautions[r.recipe_id]),
                    order[r.recipe_id],
                )
            )
            result.truncated_pools += int(len(pool) > pool_limit)
            if pool_strategy == "declared_food_stratified":
                chosen = bound_declared_food_pool(pool, old, signatures, pool_limit)
            else:
                chosen = pool[:pool_limit]
                if old not in chosen:
                    chosen = [old, *chosen[: pool_limit - 1]]
            peers.append(chosen)
        old_cost = exposure(working)
        old_objective = objective(working)
        old_goals = vector(working)
        old_saturated = saturated_goal_scores(working, constraints, rules)
        old_references = set(counts(working, references))
        old_cautions = counts(working, cautions)
        old_preferences = observe_preferences(working, requests)
        old_balance = balance_rank(working, len(working), constraints)
        old_methods = counts(working, methods)
        old_foods = set(counts(working, foods))
        old_queries = set(counts(working, queries))
        old_scoped = scoped_method_coverage(working, constraints.scoped_methods)
        best: tuple[tuple[object, ...], list[Recipe]] | None = None
        exhausted = False
        for width in (1, 2):
            for scope in combinations(range(len(working)), width):
                for replacements in product(*(peers[i] for i in scope)):
                    if result.evaluated >= evaluation_limit:
                        exhausted = True
                        break
                    result.evaluated += 1
                    proposal = list(working)
                    for i, r in zip(scope, replacements, strict=True):
                        proposal[i] = r
                    if objective(proposal) >= old_objective or exposure(proposal) > old_cost:
                        result.blockers["no_history_gain"] += 1
                        continue
                    if len({r.recipe_id for r in proposal}) != len(proposal):
                        result.blockers["duplicate_identity"] += 1
                        continue
                    new_goals = vector(proposal)
                    protected_start = 0 if policy == "additive_and_types" else health_width
                    if any(
                        a < b
                        for a, b in zip(
                            new_goals[protected_start:], old_goals[protected_start:], strict=True
                        )
                    ):
                        result.blockers["additive_goal_lower"] += 1
                        continue
                    if not old_references <= set(counts(proposal, references)):
                        result.blockers["unique_positive_type_lost"] += 1
                        continue
                    new_saturated = saturated_goal_scores(proposal, constraints, rules)
                    if any(a < b for a, b in zip(new_saturated, old_saturated, strict=True)):
                        result.blockers["saturated_goal_lower"] += 1
                        continue
                    if any(n > old_cautions[k] for k, n in counts(proposal, cautions).items()):
                        result.blockers["caution_frequency_increased"] += 1
                        continue
                    if (
                        not old_foods <= set(counts(proposal, foods))
                        or not old_queries <= set(counts(proposal, queries))
                        or not composition_satisfied(proposal, constraints, previous=working)
                        or scoped_method_coverage(proposal, constraints.scoped_methods) & old_scoped
                        != old_scoped
                        or any(
                            observe_preferences(proposal, requests)[k] < v
                            for k, v in old_preferences.items()
                        )
                    ):
                        result.blockers["explicit_coverage_lost"] += 1
                        continue
                    new_methods = counts(proposal, methods)
                    if (
                        balance_rank(proposal, len(proposal), constraints) < old_balance
                        or sum(bool(methods[r.recipe_id]) for r in proposal)
                        < sum(bool(methods[r.recipe_id]) for r in working)
                        or len(new_methods) < len(old_methods)
                        or pairs(new_methods) > pairs(old_methods)
                        or pairs(counts(proposal, families)) > pairs(counts(working, families))
                        or pairs(counts(proposal, focus)) > pairs(counts(working, focus))
                    ):
                        result.blockers["meal_method_or_food_diversity_lower"] += 1
                        continue
                    loss = tuple(max(0, b - a) for a, b in zip(new_goals, old_goals, strict=True))
                    rank: tuple[object, ...] = (
                        *objective(proposal),
                        max(loss, default=0),
                        sum(loss),
                        sum(
                            a.recipe_id != b.recipe_id
                            for a, b in zip(proposal, working, strict=True)
                        ),
                        tuple(order[r.recipe_id] for r in proposal),
                    )
                    if best is None or rank < best[0]:
                        best = (rank, proposal)
                if exhausted:
                    break
            if exhausted:
                break
        if best is not None:
            proposal = best[1]
            exchange: dict[str, object] = {
                "before_names": [r.name for r in working],
                "after_names": [r.name for r in proposal],
                "history_cost_before": old_cost,
                "history_cost_after": exposure(proposal),
                "raw_goal_sums_before": old_goals,
                "raw_goal_sums_after": vector(proposal),
                "saturated_goal_scores_before": old_saturated,
                "saturated_goal_scores_after": saturated_goal_scores(proposal, constraints, rules),
                "positive_types_before": sorted(old_references),
                "positive_types_after": sorted(counts(proposal, references)),
                "cautions_before": dict(old_cautions),
                "cautions_after": dict(counts(proposal, cautions)),
                "scene_request_types": requests.scenes,
                "scene_masks_before": [scenes[r.recipe_id] for r in working],
                "scene_masks_after": [scenes[r.recipe_id] for r in proposal],
            }
            if ranking == "declared_content":
                exchange.update(
                    ranking=ranking,
                    observation_policy_version=observation_version(feature_policy),
                    weighted_content_units_before=content_cost(working),
                    weighted_content_units_after=content_cost(proposal),
                    observed_history_pairs=[content[r.recipe_id].observed_pairs for r in proposal],
                    missing_history_pairs=[content[r.recipe_id].missing_pairs for r in proposal],
                    unrepresented_staple_pairs=[content[r.recipe_id].unrepresented_staple_pairs for r in proposal],
                )
            if preserve_name_evidence:
                exchange["observation_policy_version"] = observation_version(feature_policy)
                exchange["finite_name_evidence_preserved_NOT_full_food_quality"] = True
                exchange["observed_pairs_before"] = [content[r.recipe_id].observed_pairs for r in working]
                exchange["observed_pairs_after"] = [content[r.recipe_id].observed_pairs for r in proposal]
                exchange["missing_pairs_before"] = [content[r.recipe_id].missing_pairs for r in working]
                exchange["missing_pairs_after"] = [content[r.recipe_id].missing_pairs for r in proposal]
            result.exchanges.append(exchange)
            working = proposal
            result.recipes = working
        if exhausted:
            result.status = "budget_exhausted_best_observed_NOT_optimal"
            break
        if best is None:
            break
    return result
