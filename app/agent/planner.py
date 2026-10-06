"""Deterministic feasible menu construction with localized replacement."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.agent.dish_composition import plan_composition
from app.agent.entree_preferences import repair_entree_preferences
from app.agent.flavor_preferences import repair_flavor_preferences
from app.agent.health_preferences import HealthRepair, repair_health_preferences
from app.agent.initial_goal_priority import source_goal_frontier
from app.agent.meal_context import repair_meal_context
from app.agent.meal_structure import (
    repair_menu_roles,
    repair_shared_soup_entree,
    repair_shared_soup_staple,
)
from app.agent.menu_balance import balance_rank
from app.agent.menu_diversity import menu_similarity_penalty
from app.agent.menu_variety import VarietyRepair, repair_menu_variety
from app.agent.method_preferences import repair_method_preferences
from app.agent.next_meal_rotation import repair_next_meal_repetition
from app.agent.protein_food_preferences import repair_protein_food_preferences
from app.agent.scene_preferences import repair_scene_preferences, scene_warnings
from app.agent.scoped_methods import repair_scoped_methods
from app.domain.context_exclusions import context_conflict_issue
from app.domain.cooking_methods import cooking_method_evidence
from app.domain.culinary_focus import culinary_food_focus
from app.domain.dish_composition import composition_active, composition_issue, composition_satisfied
from app.domain.entree_preferences import mixed_entree_warnings
from app.domain.food_variety import food_families
from app.domain.health_evidence import no_goal_regression
from app.domain.heart_protein_reference import (
    named_food_tradeoff,
    preferred_protein_body,
    protein_body_tradeoff,
)
from app.domain.matching_tags import (
    flavor_conflicts,
    flavor_exclusion_hits,
    negative_flavor_menu_disclosure,
    supported_flavor_preferences,
)
from app.domain.meal_context import meal_context_warnings, meal_cost
from app.domain.meal_history import recommendation_counts
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.method_preferences import (
    method_preference_warnings,
    method_reference_mask,
    method_swap_preserves,
)
from app.domain.models import Constraints, Recipe
from app.domain.scoped_methods import (
    missing_scoped_methods,
    scoped_method_label,
    scoped_method_mask,
    scoped_method_warnings,
)
from app.retrieval.keyword import recipe_relevance_score
from app.rules.diet import diet_issue, diet_reasons
from app.rules.engine import RuleDecision, RuleEngine, compact

_DIVERSITY_POOL_LIMIT = 64


@dataclass
class PlanResult:
    recipes: list[Recipe] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failure: str | None = None


class MenuPlanner:
    def __init__(self, rule_engine: RuleEngine):
        self.rules = rule_engine

    @staticmethod
    def _is_soup(recipe: Recipe) -> bool:
        return "soup" in recipe.categories

    @staticmethod
    def _is_main_meal(recipe: Recipe) -> bool:
        return is_main_meal_recipe(recipe)

    def _cover_ingredient_preferences(
        self, recipes: list[Recipe], allowed: dict[str, Recipe],
        decisions: dict[str, RuleDecision], constraints: Constraints,
        current: list[Recipe], replace_slot: int | None, order: dict[str, int],
        allow_repair: bool = True,
    ) -> tuple[list[Recipe], set[int], list[str]]:
        """Improve menu-level ingredient coverage without relaxing hard rules.

        Each swap must add a missing preference and keep every covered one.
        This bounded heuristic is stable on repeated planning; preferences are
        not hard requirements and do not justify replacing unrelated slots in
        an explicit single-dish replacement.
        Empty continuation only discloses missing preferences; it must not
        silently reverse an accepted edit to restore an earlier soft preference.
        """
        terms = list(dict.fromkeys(
            self.rules.canonical_food(term) for term in constraints.preferred_ingredients
            if term.strip()
        ))
        if not terms:
            return recipes, set(), []
        def mask_for(recipe: Recipe) -> int:
            return sum(
                1 << index for index, term in enumerate(terms)
                if self.rules.preference_matches(recipe, term)
            )

        covers = {recipe.recipe_id: mask_for(recipe) for recipe in recipes}

        def coverage(menu: list[Recipe]) -> int:
            result = 0
            for recipe in menu:
                result |= covers[recipe.recipe_id]
            return result

        if coverage(recipes) == (1 << len(terms)) - 1:
            return recipes, set(), []
        covers.update({
            recipe_id: mask_for(recipe) for recipe_id, recipe in allowed.items()
            if recipe_id not in covers
        })
        menu = list(recipes)
        changed: set[int] = set()
        positions = (range(len(menu)) if replace_slot is None else [replace_slot - 1]) if allow_repair else ()
        # Coverage strictly increases, so at most len(terms) swaps are possible.
        for _ in terms:
            before_mask = coverage(menu)
            used = {recipe.recipe_id for recipe in menu}
            before_balance = balance_rank(menu, constraints.dish_count, constraints)
            best: tuple[tuple, int, Recipe] | None = None
            for index in positions:
                old = menu[index]
                rest = menu[:index] + menu[index + 1:]
                rest_mask = coverage(rest)
                for candidate in allowed.values():
                    if candidate.recipe_id in used or self._is_soup(candidate) != self._is_soup(old):
                        continue
                    next_mask = rest_mask | covers[candidate.recipe_id]
                    if not composition_satisfied([*rest, candidate], constraints, previous=menu):
                        continue
                    if not method_swap_preserves(menu, index, candidate, constraints.preferences, scoped=constraints.scoped_methods):
                        continue
                    if next_mask & before_mask != before_mask or next_mask == before_mask:
                        continue
                    gain = next_mask.bit_count() - before_mask.bit_count()
                    # Prefer the greatest coverage gain, then reuse an already
                    # changed slot and preserve meal categories where possible.
                    new_change = int(
                        index < len(current) and current[index].recipe_id == old.recipe_id
                    )
                    candidate_balance = balance_rank([*rest, candidate], constraints.dish_count, constraints)
                    balance_loss = tuple(
                        max(0, before - after)
                        for before, after in zip(before_balance, candidate_balance)
                    )
                    rank = (
                        -gain, new_change, balance_loss,
                        -decisions[candidate.recipe_id].score,
                        order.get(candidate.recipe_id, len(order)), index, candidate.recipe_id,
                    )
                    if best is None or rank < best[0]:
                        best = (rank, index, candidate)
            if best is None:
                break
            _, index, candidate = best
            menu[index] = candidate
            changed.add(index)
        final_mask = coverage(menu)
        missing = [term for index, term in enumerate(terms) if not final_mask & (1 << index)]
        warnings = []
        if missing:
            warnings.append(
                "当前菜单未覆盖食材偏好：" + "、".join(missing)
                + "；已保留硬约束及指定替换范围，可进一步明确要调整的菜品。"
            )
        return menu, changed, warnings

    def plan(
        self,
        candidates: list[Recipe],
        constraints: Constraints,
        current: list[Recipe] | None = None,
        replace_slot: int | None = None,
        reject_ids: set[str] | None = None,
        query_terms: list[str] | None = None,
        recheck_soft_preferences: bool = True,
        experiment_menu_variety: bool | Literal["culinary_focus", "culinary_focus_guarded"] = False,
        experiment_initial_goal_frontier: bool = False,
        recent_recipe_names: Sequence[Sequence[str]] | None = None,
        allow_adjacent_rotation: bool = False,
    ) -> PlanResult:
        current = current or []
        if issue := context_conflict_issue(constraints):
            return PlanResult(failure=issue)
        if conflict := flavor_conflicts(constraints.preferences):
            return PlanResult(failure="已记录的正负口味偏好存在冲突：" + "、".join(conflict)
                              + "；请明确确认保留哪项，不自动删除旧偏好。")
        rejected = set(reject_ids or set())
        rejected_names = {compact(r.name) for r in current if r.recipe_id in rejected}
        count, soups = constraints.dish_count, constraints.soup_count
        if issue := diet_issue(constraints):
            return PlanResult(failure=issue)
        if issue := composition_issue(constraints):
            return PlanResult(failure=issue)
        if soups > count:
            return PlanResult(failure="汤的数量不能超过总菜数；总菜数包含汤。")
        if constraints.max_minutes is not None:
            return PlanResult(failure=(
                f"菜谱缺少可核验的总烹饪时间，无法保证 {constraints.max_minutes} 分钟内完成，"
                "请明确是否放宽时间限制。"
            ))
        unresolved = self.rules.unresolved_allergies(constraints)
        if unresolved:
            return PlanResult(failure="暂未支持该过敏原的可靠映射，需要补充或审核：" + "、".join(unresolved))
        if replace_slot is not None:
            if replace_slot < 1 or replace_slot > len(current):
                return PlanResult(failure="指定的换菜序号不存在，需要明确当前菜单中的目标菜品。")
            if replace_slot > count:
                return PlanResult(failure="指定换菜位置超出调整后的总菜数，请明确保留几道及要替换的位置。")
            rejected.add(current[replace_slot - 1].recipe_id)
            rejected_names.add(compact(current[replace_slot - 1].name))

        # The caller passes the full sorted catalog (or retries with it). Keep
        # current records available to preserve valid, unrelated slots.
        catalog = {recipe.recipe_id: recipe for recipe in [*current, *candidates]}
        decisions: dict[str, RuleDecision] = {}
        allowed: dict[str, Recipe] = {}
        seen_names: set[str] = set()
        for recipe_id, recipe in catalog.items():
            if (
                recipe_id in rejected
                or compact(recipe.name) in rejected_names
                or not recipe.eligible
                or not self._is_main_meal(recipe)
            ):
                continue
            decision = self.rules.evaluate(recipe, constraints)
            decisions[recipe_id] = decision
            if decision.allowed and compact(recipe.name) not in seen_names:
                allowed[recipe_id] = recipe
                seen_names.add(compact(recipe.name))

        soup_pool = [r for r in allowed.values() if self._is_soup(r)]
        other_pool = [r for r in allowed.values() if not self._is_soup(r)]
        if len(soup_pool) < soups or len(other_pool) < count - soups:
            return PlanResult(failure=(
                f"当前审核范围内可用汤 {len(soup_pool)} 道、其他菜 {len(other_pool)} 道，"
                f"无法组成总计 {count} 道（含 {soups} 道汤）的菜单。"
                "已排除饮料、甜品、加工步骤、未拆分多菜套餐及未确认正餐角色的记录。"
            ))
        if replace_slot is not None and any(
            index != replace_slot - 1 and (
                not self._is_main_meal(recipe) or bool(diet_reasons(recipe, constraints))
                or bool(flavor_exclusion_hits(recipe, constraints.preferences))
                # A newly rejected hard-rule source in an unrelated slot is
                # not permission to silently rebuild that slot during a local
                # replacement (including ingredient-only non-spicy evidence).
                or not self.rules.evaluate(recipe, constraints).allowed
            )
            for index, recipe in enumerate(current[:count])
        ):
            return PlanResult(failure="当前整餐限制或菜位资格需要改变其他菜位，不能只换指定菜；请确认是否允许整餐调整。")

        slots: list[Recipe | None] = [None] * count
        used: set[str] = set()
        preserved_soups = preserved_other = 0
        for index, recipe in enumerate(current[:count]):
            if recipe.recipe_id not in allowed or recipe.recipe_id in used:
                continue
            is_soup = self._is_soup(recipe)
            if is_soup and preserved_soups >= soups:
                continue
            if not is_soup and preserved_other >= count - soups:
                continue
            slots[index] = allowed[recipe.recipe_id]
            used.add(recipe.recipe_id)
            preserved_soups += int(is_soup)
            preserved_other += int(not is_soup)

        # Retain legal slots provisionally. Missing contextual roles are repaired
        # after food preferences, except when the user requested a local edit.
        order = {recipe.recipe_id: i for i, recipe in enumerate(candidates)}
        exposures = recommendation_counts(recent_recipe_names or [])
        scoped_health_evidence = {
            goal: {key: self.rules.goal_evidence(r, goal) for key, r in allowed.items()}
            for goal in dict.fromkeys(constraints.health_goals)
            if self.rules.config["health_goals"].get(goal, {}).get("scope_category_to_role")
        }
        relevance_cache: dict[str, float] = {}
        # Explicit offline ablation only: service/API calls never enable this.
        # Unknown goals have no configured evidence and cannot create priority.
        active_goals = [
            index for index, goal in enumerate(dict.fromkeys(constraints.health_goals))
            if goal in self.rules.config["health_goals"]
        ] if experiment_initial_goal_frontier else []
        initial_goal_scores: dict[str, tuple[int, ...]] = {}
        if active_goals:
            for recipe_id, recipe in allowed.items():
                vector = self.rules.health_scores(recipe, constraints)
                initial_goal_scores[recipe_id] = tuple(vector[i] for i in active_goals)

        def relevance_for(recipe: Recipe) -> float:
            if recipe.recipe_id not in relevance_cache:
                relevance_cache[recipe.recipe_id] = recipe_relevance_score(
                    recipe, query_terms or [], constraints, self.rules,
                )
            return relevance_cache[recipe.recipe_id]

        def choose(pool: list[Recipe], selected: list[Recipe] | None = None, slot: int | None = None) -> Recipe:
            if selected is None:
                selected = [item for item in slots if item is not None]
            selected_ids = {item.recipe_id for item in selected}
            ranked = [
                (recipe, balance_rank([*selected, recipe], count, constraints))
                for recipe in pool if recipe.recipe_id not in selected_ids
            ]
            # Keep role coverage first, then source meal context before health
            # and query ties. Meal labels are not hard nutritional guarantees.
            role_width = len(balance_rank([], count, constraints, roles_only=True))
            best_roles = max(balance[:role_width] for _, balance in ranked)
            balances = {recipe.recipe_id: balance for recipe, balance in ranked}
            balanced = [recipe for recipe, balance in ranked if balance[:role_width] == best_roles]
            if constraints.method_meal_priority == "method":
                covered = 0
                for record in selected:
                    covered |= method_reference_mask(record, constraints.preferences)
                gains = {recipe.recipe_id: (
                    method_reference_mask(recipe, constraints.preferences) & ~covered
                ).bit_count() for recipe in balanced}
                best_method = max(gains.values())
                balanced = [recipe for recipe in balanced if gains[recipe.recipe_id] == best_method]
            best_context = min(meal_cost(recipe, constraints.meal_type) for recipe in balanced)
            balanced = [recipe for recipe in balanced
                        if meal_cost(recipe, constraints.meal_type) == best_context]
            if constraints.scoped_methods:
                # selected is compacted and cannot establish original slot
                # indices. Only unscoped food targets may count as covered here.
                covered_scoped = 0
                for record in selected:
                    covered_scoped |= scoped_method_mask(record, constraints.scoped_methods, slot=None)
                requested_mask = sum(1 << i for i, request in enumerate(constraints.scoped_methods) if request.required)
                gains_scoped = {recipe.recipe_id: scoped_method_mask(
                    recipe, constraints.scoped_methods, slot=slot
                ) & ~covered_scoped for recipe in balanced}
                priorities = {key: ((gain & requested_mask).bit_count(), gain.bit_count())
                              for key, gain in gains_scoped.items()}
                best_scoped = max(priorities.values())
                balanced = [recipe for recipe in balanced if priorities[recipe.recipe_id] == best_scoped]
            if experiment_initial_goal_frontier:
                balanced = source_goal_frontier(balanced, initial_goal_scores)
            # Only an explicit method-priority choice may precede meal context.
            # Known/unknown evidence and concentration cannot override dinner
            # labels and then rely on a later repair blocked by method guards.
            best_balance = max(balances[recipe.recipe_id] for recipe in balanced)
            if not scoped_health_evidence or constraints.method_meal_priority == "method":
                balanced = [recipe for recipe in balanced if balances[recipe.recipe_id] == best_balance]
            # Reuse the configured food credit once per verified menu reference.
            # A second tofu/oat reference is not a second health achievement.
            covered_goal_terms: dict[str, set[str]] = {}
            for goal, evidence_by_id in scoped_health_evidence.items():
                covered_goal_terms[goal] = set().union(*(
                    set(e.preferred_rank_terms or ())
                    for record in selected
                    if (e := evidence_by_id[record.recipe_id]) is not None
                ))

            def menu_rule_score(recipe: Recipe) -> float:
                value = decisions[recipe.recipe_id].score
                for goal, covered_terms in covered_goal_terms.items():
                    evidence = scoped_health_evidence[goal][recipe.recipe_id]
                    if evidence is not None:
                        value += evidence.score_with_covered_terms(covered_terms) - evidence.score
                return value

            best_rule_score = max(menu_rule_score(recipe) for recipe in balanced)
            equally_suitable = [
                recipe for recipe in balanced
                if menu_rule_score(recipe) == best_rule_score
            ]
            best_peer_balance = max(balances[r.recipe_id] for r in equally_suitable)
            equally_suitable = [r for r in equally_suitable if balances[r.recipe_id] == best_peer_balance]
            # Compute query relevance before bounding the expensive
            # similarity comparison, so a late explicit match is not lost.
            relevance = {recipe.recipe_id: relevance_for(recipe) for recipe in equally_suitable}
            equally_suitable.sort(key=lambda recipe: (
                -relevance[recipe.recipe_id],
                order.get(recipe.recipe_id, len(order)), recipe.recipe_id,
            ))
            finalists = equally_suitable[:_DIVERSITY_POOL_LIMIT]
            best_relevance = relevance[finalists[0].recipe_id]
            baseline = min(
                (recipe for recipe in finalists if relevance[recipe.recipe_id] == best_relevance),
                key=lambda recipe: (
                    menu_similarity_penalty(recipe, selected),
                    order.get(recipe.recipe_id, len(order)),
                    recipe.recipe_id,
                ),
            )
            if not exposures:
                return baseline
            # Rotation is a final tie-break within the SAME suitability and
            # within-meal source similarity. Do not trade any configured goal
            # or unique preferred-food/method reference for less repetition.
            goal_base = self.rules.soft_goal_scores(baseline, constraints)
            reference_base = {
                goal: self.rules.goal_evidence(baseline, goal)
                for goal in dict.fromkeys(constraints.health_goals)
            }

            def retains_references(candidate: Recipe) -> bool:
                for goal, previous in reference_base.items():
                    new = self.rules.goal_evidence(candidate, goal)
                    if previous is None:
                        continue
                    if (
                        new is None
                        or not set(previous.preferred_foods) <= set(new.preferred_foods)
                        or not set(previous.good_methods) <= set(new.good_methods)
                        or (previous.category_rank_enabled and previous.category_foods
                            and not (new.category_rank_enabled and new.category_foods))
                    ):
                        return False
                    if not set((
                        *new.discouraged_foods, *new.raw_cautions,
                        *new.step_cautions, *new.bad_methods,
                    )) <= set((
                        *previous.discouraged_foods, *previous.raw_cautions,
                        *previous.step_cautions, *previous.bad_methods,
                    )):
                        return False
                return True

            peers = [
                r for r in finalists
                if relevance[r.recipe_id] == best_relevance
                and menu_similarity_penalty(r, selected) == menu_similarity_penalty(baseline, selected)
                and no_goal_regression(self.rules.soft_goal_scores(r, constraints), goal_base)
                and retains_references(r)
            ]
            return min(peers, key=lambda r: (
                exposures[compact(r.name)], order.get(r.recipe_id, len(order)), r.recipe_id,
            ))

        if composition_active(constraints):
            composition = plan_composition(list(allowed.values()), constraints, current,
                                           choose=choose, replace_slot=replace_slot, choose_slot=choose)
            if composition.failure:
                return PlanResult(failure=composition.failure)
            recipes = composition.recipes
        else:
            soups_needed = soups - preserved_soups
            for index, recipe in enumerate(slots):
                if recipe is not None:
                    continue
                # A replaced soup stays in the same slot when a soup is still due.
                old_was_soup = index < len(current) and self._is_soup(current[index])
                free_after = sum(item is None for item in slots[index + 1:])
                wants_soup = soups_needed > 0 and (old_was_soup or free_after < soups_needed)
                chosen = choose(soup_pool if wants_soup else other_pool, slot=index + 1)
                slots[index] = chosen
                used.add(chosen.recipe_id)
                soups_needed -= int(wants_soup)
            recipes = [recipe for recipe in slots if recipe is not None]
        # A named protein-food insertion also covers its raw ingredient. Do it
        # before broad ingredient coverage to avoid changing an unrelated slot
        # first and then changing a second slot for the same new preference.
        protein_goal_scores = {
            key: self.rules.soft_goal_scores(r, constraints) for key, r in allowed.items()
        } if constraints.preferred_ingredients else {}
        protein_food_early = repair_protein_food_preferences(
            recipes, list(allowed.values()), constraints,
            canonical_food=self.rules.canonical_food,
            food_matches=lambda r, term: bool(self.rules.preference_matches(r, term)),
            goal_scores=protein_goal_scores,
            order=order, replace_slot=replace_slot, allow_repair=recheck_soft_preferences,
        )
        recipes = protein_food_early.recipes
        recipes, preference_changes, preference_warnings = self._cover_ingredient_preferences(
            recipes, allowed, decisions, constraints, current, replace_slot, order,
            allow_repair=recheck_soft_preferences,
        )
        structure = repair_menu_roles(
            recipes, list(allowed.values()), constraints, original=current,
            scores={key: decision.score for key, decision in decisions.items()},
            order=order, food_matches=lambda recipe, term: bool(self.rules.preference_matches(recipe, term)),
            replace_slot=replace_slot,
            allow_repair=recheck_soft_preferences,
        )
        recipes = structure.recipes
        entree = repair_entree_preferences(
            recipes, list(allowed.values()), constraints, order=order,
            food_matches=lambda recipe, term: bool(self.rules.preference_matches(recipe, term)),
            replace_slot=replace_slot, allow_repair=recheck_soft_preferences,
        )
        recipes = entree.recipes
        scoped = repair_scoped_methods(
            recipes, list(allowed.values()), constraints, order=order,
            food_matches=lambda r, term: bool(self.rules.preference_matches(r, term)),
            replace_slot=replace_slot, allow_repair=recheck_soft_preferences,
        )
        recipes = scoped.recipes
        context = repair_meal_context(
            recipes, list(allowed.values()), constraints,
            scores={key: decision.score for key, decision in decisions.items()}, order=order,
            food_matches=lambda recipe, term: bool(self.rules.preference_matches(recipe, term)),
            replace_slot=replace_slot,
            goal_scores={key: self.rules.soft_goal_scores(r, constraints)
                         for key, r in allowed.items()},
            allow_repair=recheck_soft_preferences,
        )
        recipes = context.recipes
        health = repair_health_preferences(
            recipes, list(allowed.values()), constraints,
            scores={key: self.rules.soft_goal_scores(recipe, constraints) for key, recipe in allowed.items()}
            if recheck_soft_preferences else {},
            order=order, food_matches=lambda recipe, term: bool(self.rules.preference_matches(recipe, term)),
            replace_slot=replace_slot,
            allow_diversity_tradeoff=(
                any(goal in self.rules.config["health_goals"] for goal in constraints.health_goals)
                and constraints.method_meal_priority != "method"
            ),
            goal_evidence=self.rules.goal_evidence,
            protected_food_terms=query_terms or (),
        )
        recipes = health.recipes
        variety = VarietyRepair(recipes, frozenset())
        # Ordinary continuation must not churn a retained legal menu. The
        # caller explicitly authorizes soft rechecks for new/updated planning.
        # Experimental only until independent source review demonstrates net
        # benefit. API/service callers never enable it implicitly.
        if recheck_soft_preferences and experiment_menu_variety:
            variety = repair_menu_variety(
                recipes, list(allowed.values()), constraints,
                goal_scores={key: self.rules.soft_goal_scores(r, constraints) for key, r in allowed.items()},
                rule_scores={key: decisions[key].score for key in allowed},
                relevance={key: relevance_for(r) for key, r in allowed.items()},
                order=order, food_matches=lambda r, term: bool(self.rules.preference_matches(r, term)),
                replace_slot=replace_slot,
                family_evidence={key: culinary_food_focus(r).families for key, r in allowed.items()}
                if experiment_menu_variety in ("culinary_focus", "culinary_focus_guarded") else None,
                method_evidence={key: frozenset(cooking_method_evidence(r).main_methods)
                                 for key, r in allowed.items()}
                if experiment_menu_variety == "culinary_focus_guarded" else None,
                declared_family_evidence={key: food_families(r) for key, r in allowed.items()}
                if experiment_menu_variety == "culinary_focus_guarded" else None,
            )
            recipes = variety.recipes
        flavor = repair_flavor_preferences(
            recipes, list(allowed.values()), constraints,
            goal_scores={key: self.rules.soft_goal_scores(r, constraints)
                         for key, r in allowed.items()},
            order=order,
            food_matches=lambda r, term: bool(self.rules.preference_matches(r, term)),
            replace_slot=replace_slot,
            allow_repair=recheck_soft_preferences,
        )
        recipes = flavor.recipes
        scene = repair_scene_preferences(
            recipes, list(allowed.values()), constraints,
            goal_scores={key: self.rules.soft_goal_scores(r, constraints)
                         for key, r in allowed.items()},
            order=order,
            food_matches=lambda r, term: bool(self.rules.preference_matches(r, term)),
            replace_slot=replace_slot, allow_repair=recheck_soft_preferences,
            protected_food_terms=query_terms or (),
        )
        recipes = scene.recipes
        method = repair_method_preferences(
            recipes, list(allowed.values()), constraints,
            goal_scores={key: self.rules.soft_goal_scores(r, constraints) for key, r in allowed.items()},
            order=order, food_matches=lambda r, term: bool(self.rules.preference_matches(r, term)),
            replace_slot=replace_slot, allow_repair=recheck_soft_preferences,
        )
        recipes = method.recipes
        protein_food = repair_protein_food_preferences(
            recipes, list(allowed.values()), constraints,
            canonical_food=self.rules.canonical_food,
            food_matches=lambda r, term: bool(self.rules.preference_matches(r, term)),
            goal_scores=protein_goal_scores,
            order=order, replace_slot=replace_slot, allow_repair=recheck_soft_preferences,
            source_food_tradeoff=lambda old, new: named_food_tradeoff(
                old, new, constraints.health_goals,
                [self.rules.canonical_food(t) for t in constraints.preferred_ingredients],
                self.rules.goal_evidence,
            ),
        )
        recipes = protein_food.recipes
        shared_staple = repair_shared_soup_staple(
            recipes, list(allowed.values()), constraints,
            scores={key: decision.score for key, decision in decisions.items()},
            order=order, food_matches=lambda r, term: bool(self.rules.preference_matches(r, term)),
            query_terms=query_terms or (), replace_slot=replace_slot,
            allow_repair=recheck_soft_preferences,
        )
        recipes = shared_staple.recipes
        shared_entree = repair_shared_soup_entree(
            recipes, list(allowed.values()), constraints,
            scores={key: decision.score for key, decision in decisions.items()},
            order=order, food_matches=lambda r, term: bool(self.rules.preference_matches(r, term)),
            query_terms=query_terms or (), replace_slot=replace_slot,
            allow_repair=recheck_soft_preferences,
        )
        recipes = shared_entree.recipes
        # Run the narrow protein-body choice after structural/default repairs.
        # Otherwise generic method repair can undo it and churn the staple.
        # This source-only pass never changes other roles or relaxes hard rules.
        heart_body = HealthRepair(recipes, frozenset())
        if recheck_soft_preferences and "护心" in constraints.health_goals:
            heart_body = repair_health_preferences(
                recipes, list(allowed.values()), constraints,
                scores={key: self.rules.soft_goal_scores(r, constraints) for key, r in allowed.items()},
                order=order, food_matches=lambda r, term: bool(self.rules.preference_matches(r, term)),
                replace_slot=replace_slot,
                allow_diversity_tradeoff=constraints.method_meal_priority != "method",
                protected_food_terms=query_terms or (),
                source_food_reference=preferred_protein_body,
                source_food_tradeoff=lambda old, new: protein_body_tradeoff(
                    old, new, constraints.health_goals, self.rules.goal_evidence,
                ),
            )
            recipes = heart_body.recipes
        health = HealthRepair(
            recipes, health.changed_indices | heart_body.changed_indices,
            health.diversity_tradeoff_indices | heart_body.diversity_tradeoff_indices,
            heart_body.source_food_tradeoff_indices,
        )
        rotation = VarietyRepair(recipes, frozenset())
        # Only the caller's authorized new-meal branch supplies history without
        # a retained current menu. Continuation/local edits must never rotate.
        if (allow_adjacent_rotation and recheck_soft_preferences and recent_recipe_names
                and not current and replace_slot is None):
            rotation = repair_next_meal_repetition(
                recipes, list(allowed.values()), constraints,
                recent_recipe_names=recent_recipe_names, order=order,
                relevance=relevance_for,
                goal_scores={key: self.rules.soft_goal_scores(r, constraints) for key, r in allowed.items()},
                goal_evidence=self.rules.goal_evidence,
                food_matches=lambda r, term: bool(self.rules.preference_matches(r, term)),
                protected_food_terms=query_terms or (),
            )
            recipes = rotation.recipes
        used = {recipe.recipe_id for recipe in recipes}
        if len(recipes) != count or len(used) != count or sum(self._is_soup(r) for r in recipes) != soups:
            return PlanResult(failure="菜单结构校验失败，没有输出未经验证的菜单。")
        if not all(self.rules.evaluate(r, constraints).allowed for r in recipes):
            return PlanResult(failure="最终约束校验失败，没有输出未经验证的菜单。")
        if not composition_satisfied(recipes, constraints):
            return PlanResult(failure="最终荤素数量校验失败，没有输出未经验证的菜单。")
        if missing := missing_scoped_methods(recipes, constraints.scoped_methods, required_only=True):
            return PlanResult(failure="当前安全候选及授权范围的有界同角色调整未满足明确食材／菜位做法："
                              + "、".join(map(scoped_method_label, missing))
                              + "；未输出替代做法，不改写原步骤或扩大换菜范围，这不是全库无解证明。")
        warnings = list(dict.fromkeys(warning for r in recipes for warning in decisions[r.recipe_id].warnings))
        warnings.extend(preference_warnings)
        warnings.extend(protein_food.warnings)
        warnings.extend(flavor.warnings)
        warnings.extend(negative_flavor_menu_disclosure(recipes, constraints.preferences))
        warnings.extend(scene_warnings(recipes, constraints))
        warnings.extend(meal_context_warnings(recipes, constraints.meal_type))
        warnings.extend(method_preference_warnings(recipes, constraints.preferences))
        warnings.extend(scoped_method_warnings(recipes, constraints.scoped_methods))
        warnings.extend(mixed_entree_warnings(recipes, constraints))
        if rotation.changed_indices:
            warnings.append(
                "明确下一餐规划中，以完整原方的同角色菜替换近期已推荐的重复菜；"
                "保留已覆盖的食材、口味、场景和点名做法，"
                "检索相关度与清淡蒸煮参考加分、未要求的一般做法分布、冷热与同餐相似度可取舍；"
                "清淡原标签不等于少油少盐，各菜调味仍需核对。"
                "历史只记录推荐，不代表实际食用；不证明份量、味觉或营养达标。"
            )
        if health.diversity_tradeoff_indices:
            warnings.append(
                (
                    "为优先有完整原方主体依据的鱼/豆腐或瘦禽肉，本轮允许一般做法多样性取舍；"
                    if health.source_food_tradeoff_indices else
                    "为减少原方已声明的需核用量配料或做法，本轮允许一般做法多样性取舍；"
                ) +
                "仍保留已覆盖的点名做法和指定菜位要求。"
                "含盐不等于高钠，未列盐不等于无钠；"
                "不表示低钠、护心、营养或份量达标，需核每人全天总摄入。"
            )
        if health.source_food_tradeoff_indices:
            warnings.append(
                "护心食物选择参考：以原料、菜名和成菜角色共同支持的鱼/豆腐或瘦禽肉替换明确红肉主体；"
                "不是仅凭辅料加分，也不把红肉认定为高脂或禁用。"
                "新方普通盐/含钠调味来源可能增加，内部含钠来源参考分允许取舍；"
                "用量、品牌、分餐及全天摄入仍需核，不宣称低钠、护心功效或整体营养更优。"
            )
        if shared_entree.changed_indices:
            warnings.append(
                "多人有汤正餐中，以原方有成菜主体依据的蛋白菜替换未点名的蒸蛋；"
                "蛋羹并非汤或不安全菜，不强制肉菜数、不表示健康或每人份量达标。"
            )
            if any(not cooking_method_evidence(recipes[i]).main_methods for i in shared_entree.changed_indices):
                warnings.append(
                    "新菜原步骤含成形主体及机器烹饪，但未提供可核验的具体做法程序；"
                    "请核对原设备说明，不据菜名补写蒸法、温度、时长或熟度保证。"
                )
        if constraints.health_goals or "清淡" in supported_flavor_preferences(constraints.preferences):
            warnings.append(
                "多人正餐已含汤，本轮调整未点名的粥或蒸蛋叠加；"
                "实际成餐结构优先于内部健康参考分或未要求的做法多样性，"
                "不表示低钠、营养摄入或健康效果达标。"
                if shared_staple.changed_indices or shared_entree.changed_indices else
                "已在同成菜角色和授权范围复核蛋白主体食物方向，含钠来源代理与一般方法分布可取舍；"
                "其他目标及明确需求仍保护，不把此取舍称为摄入达标。"
                if health.source_food_tradeoff_indices or any(
                    text.startswith("为优先有完整原方主体依据")
                    for text in protein_food.warnings
                ) else
                "已在同成菜角色、已覆盖食材偏好及指定换菜范围内复核定性目标；"
                "不降低已知餐次匹配，不用一个目标的加分抵消另一个目标的退步。"
                "保留原菜或规则排序改善都不表示营养摄入、低钠或健康效果达标。"
            )
        if structure.gaps:
            role_labels = {"vegetable": "蔬菜类菜", "protein": "蛋白质来源菜", "staple": "主食"}
            missing = "、".join(f"{role_labels[role]} {count} 道" for role, count in structure.gaps.items())
            boundary = "本轮保持指定换菜范围" if replace_slot is not None else "当前安全候选及已覆盖偏好下未能补齐"
            warnings.append(
                "本餐工程角色目标尚缺：" + missing + "；" + boundary
                + "。这是成菜结构提示，不代表营养摄入或荤素份量达标。"
            )
        if constraints.people > 1:
            warnings.append("共享菜单按已提供的全桌限制筛选；未计算每人份量或实际营养摄入。")
        changes: list[dict] = []
        for index in range(max(len(current), len(recipes))):
            before = current[index] if index < len(current) else None
            after = recipes[index] if index < len(recipes) else None
            if (before.recipe_id if before else None) == (after.recipe_id if after else None):
                continue
            if before is None:
                reason = "增加菜单菜品"
            elif index + 1 == replace_slot:
                reason = "按用户指定替换"
            elif before.recipe_id not in allowed:
                reason = "原菜命中当前限制或已被否定"
            elif index in rotation.changed_indices:
                reason = "明确下一餐中减少实际近期推荐重复，同角色保已覆盖需求，检索及未点名蒸煮代理可取舍"
            elif index in shared_staple.changed_indices:
                reason = "多人正餐已含汤，以原配方米饭替换未点名的粥；软参考允许取舍，非营养或份量达标"
            elif index in shared_entree.changed_indices:
                reason = "多人正餐已含汤，以原方成菜主体替换未点名的蒸蛋；不推断肉菜配额、程序或营养份量"
            elif index in protein_food.changed_indices | protein_food_early.changed_indices:
                reason = "为补齐有原料与菜名依据的蛋白菜食材偏好参考而同角色调整，不代表食材比例或营养达标"
            elif index in scoped.changed_positions:
                reason = "为兑现明确食材／菜位做法而同角色调整，软口味或目标参考可能有取舍；非营养或烹饪质量认证"
            elif index in method.changed_positions:
                reason = "为补齐明确做法的源成菜动作参考或明确做法多样性而同角色调整，非烹饪质量认证"
            elif index in scene.changed_positions:
                reason = (
                    "按便当原方准备参考同角色调整，减少另用模具／裱花袋；仍需原设备，不认证时间或保存安全"
                    if index in scene.setup_changed_positions else
                    "为增加该菜位的用餐场景来源参考而同角色调整"
                )
            elif index in flavor.changed_positions:
                reason = "为补齐有来源参考的口味偏好而同角色调整"
            elif index in preference_changes:
                reason = "为覆盖本餐食材偏好而局部调整"
            elif index in structure.changed_indices:
                reason = "为补齐本餐成菜角色目标而局部调整"
            elif index in entree.changed_positions:
                reason = "为补齐本餐肉鱼菜与无肉菜的有限来源参考而同角色调整，结构要求优先于定性参考分，不代表份量或健康达标"
            elif index in context.changed_indices:
                reason = "为优先参考原餐次标签或有原配方依据的助手餐次参考而同角色调整，非适配认证"
            elif index in health.changed_indices:
                reason = (
                    "按护心食物方向用有完整原方依据的蛋白主体替代明确红肉，含钠来源代理可取舍，非低钠/功效/份量认证"
                    if index in health.source_food_tradeoff_indices else
                    "为改善健康目标或做法的定性偏好而同角色调整"
                )
            elif index in variety.changed_indices:
                reason = "为减少本餐已声明食材重复而同等适配局部调整"
            elif composition_active(constraints):
                reason = "为满足本餐明确荤菜/素菜数量而调整"
            else:
                reason = "按当前菜数或汤数要求调整"
            changes.append({
                "slot": index + 1,
                "old_recipe_id": before.recipe_id if before else None,
                "new_recipe_id": after.recipe_id if after else None,
                "reason": reason,
            })
        return PlanResult(recipes, changes, warnings)
