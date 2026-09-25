"""Deterministic feasible menu construction with localized replacement."""

from dataclasses import dataclass, field

from app.agent.menu_balance import balance_rank
from app.domain.models import Constraints, Recipe
from app.rules.engine import RuleDecision, RuleEngine, compact


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
        categories = set(recipe.categories)
        return not bool(categories & {"dessert", "drink", "component"})

    def _cover_ingredient_preferences(
        self, recipes: list[Recipe], allowed: dict[str, Recipe],
        decisions: dict[str, RuleDecision], constraints: Constraints,
        current: list[Recipe], replace_slot: int | None, order: dict[str, int],
    ) -> tuple[list[Recipe], set[int], list[str]]:
        """Improve menu-level ingredient coverage without relaxing hard rules.

        Each swap must add a missing preference and keep every covered one.
        This bounded heuristic is stable on repeated planning; preferences are
        not hard requirements and do not justify replacing unrelated slots in
        an explicit single-dish replacement.
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
                if self.rules.food_matches(recipe, term)
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
        positions = range(len(menu)) if replace_slot is None else [replace_slot - 1]
        # Coverage strictly increases, so at most len(terms) swaps are possible.
        for _ in terms:
            before_mask = coverage(menu)
            used = {recipe.recipe_id for recipe in menu}
            before_balance = balance_rank(menu, constraints.dish_count)
            best: tuple[tuple, int, Recipe] | None = None
            for index in positions:
                old = menu[index]
                rest = menu[:index] + menu[index + 1:]
                rest_mask = coverage(rest)
                for candidate in allowed.values():
                    if candidate.recipe_id in used or self._is_soup(candidate) != self._is_soup(old):
                        continue
                    next_mask = rest_mask | covers[candidate.recipe_id]
                    if next_mask & before_mask != before_mask or next_mask == before_mask:
                        continue
                    gain = next_mask.bit_count() - before_mask.bit_count()
                    # Prefer the greatest coverage gain, then reuse an already
                    # changed slot and preserve meal categories where possible.
                    new_change = int(
                        index < len(current) and current[index].recipe_id == old.recipe_id
                    )
                    candidate_balance = balance_rank([*rest, candidate], constraints.dish_count)
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
    ) -> PlanResult:
        current = current or []
        rejected = set(reject_ids or set())
        rejected_names = {compact(r.name) for r in current if r.recipe_id in rejected}
        count, soups = constraints.dish_count, constraints.soup_count
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
            if recipe_id in rejected or compact(recipe.name) in rejected_names or not self._is_main_meal(recipe):
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
            ))

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

        # Soft category coverage never displaces already-valid dishes. It only
        # ranks free slots; every chosen recipe was independently screened.
        order = {recipe.recipe_id: i for i, recipe in enumerate(candidates)}

        def choose(pool: list[Recipe]) -> Recipe:
            selected = [item for item in slots if item is not None]
            def rank(recipe: Recipe) -> tuple:
                balance = balance_rank([*selected, recipe], count)
                return (
                    *(-value for value in balance),
                    -decisions[recipe.recipe_id].score,
                    order.get(recipe.recipe_id, len(order)),
                    recipe.recipe_id,
                )

            return min((r for r in pool if r.recipe_id not in used), key=rank)

        soups_needed = soups - preserved_soups
        for index, recipe in enumerate(slots):
            if recipe is not None:
                continue
            # A replaced soup stays in the same slot when a soup is still due.
            old_was_soup = index < len(current) and self._is_soup(current[index])
            free_after = sum(item is None for item in slots[index + 1:])
            wants_soup = soups_needed > 0 and (old_was_soup or free_after < soups_needed)
            chosen = choose(soup_pool if wants_soup else other_pool)
            slots[index] = chosen
            used.add(chosen.recipe_id)
            soups_needed -= int(wants_soup)

        recipes = [recipe for recipe in slots if recipe is not None]
        recipes, preference_changes, preference_warnings = self._cover_ingredient_preferences(
            recipes, allowed, decisions, constraints, current, replace_slot, order,
        )
        used = {recipe.recipe_id for recipe in recipes}
        if len(recipes) != count or len(used) != count or sum(self._is_soup(r) for r in recipes) != soups:
            return PlanResult(failure="菜单结构校验失败，没有输出未经验证的菜单。")
        if not all(self.rules.evaluate(r, constraints).allowed for r in recipes):
            return PlanResult(failure="最终约束校验失败，没有输出未经验证的菜单。")
        warnings = list(dict.fromkeys(warning for r in recipes for warning in decisions[r.recipe_id].warnings))
        warnings.extend(preference_warnings)
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
            elif index in preference_changes:
                reason = "为覆盖本餐食材偏好而局部调整"
            else:
                reason = "按当前菜数或汤数要求调整"
            changes.append({
                "slot": index + 1,
                "old_recipe_id": before.recipe_id if before else None,
                "new_recipe_id": after.recipe_id if after else None,
                "reason": reason,
            })
        return PlanResult(recipes, changes, warnings)
