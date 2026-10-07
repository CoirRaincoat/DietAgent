"""Shared initial planning and replacement tool with constraint enforcement."""

from app.agent.batch_edit import plan_batch_edit
from app.agent.planner import MenuPlanner, PlanResult, ScopedCandidateUnavailable
from app.domain.models import Constraints, Recipe
from app.domain.slot_food_exclusions import scoped_food_menu_issue, slot_food_allowed


class MenuModifyTool:
    def __init__(self, planner: MenuPlanner):
        self.planner = planner

    def __call__(
        self, candidates: list[Recipe], constraints: Constraints,
        current: list[Recipe] | None = None, replace_slot: int | None = None,
        reject_ids: set[str] | None = None,
        query_terms: list[str] | None = None,
        recheck_soft_preferences: bool = True,
        recent_recipe_names: list[list[str]] | None = None,
        allow_adjacent_rotation: bool = False,
        replace_slots: list[int] | None = None,
    ) -> PlanResult:
        if replace_slots:
            return plan_batch_edit(self.planner, candidates, constraints, current or [],
                                   replace_slots, reject_ids, query_terms, recheck_soft_preferences)
        # A later one-slot edit must keep previously confirmed local dislikes.
        if replace_slot is not None and constraints.slot_food_exclusions:
            return plan_batch_edit(self.planner, candidates, constraints, current or [],
                                   [replace_slot], reject_ids, query_terms, recheck_soft_preferences)
        try:
            result = self.planner.plan(
                candidates,
                constraints,
                current=current,
                replace_slot=replace_slot,
                reject_ids=reject_ids,
                query_terms=query_terms,
                recheck_soft_preferences=recheck_soft_preferences,
                recent_recipe_names=recent_recipe_names,
                allow_adjacent_rotation=allow_adjacent_rotation,
            )
        except ScopedCandidateUnavailable as error:
            return PlanResult(failure=str(error))
        if not result.failure and constraints.slot_food_exclusions:
            invalid = [i for i, r in enumerate(result.recipes, 1)
                       if not slot_food_allowed(r, i, constraints, self.planner.rules)]
            if invalid:
                result = plan_batch_edit(self.planner, candidates, constraints, result.recipes,
                                         invalid, reject_ids, query_terms, recheck_soft_preferences)
            if not result.failure and (issue := scoped_food_menu_issue(result.recipes, constraints, self.planner.rules)):
                return PlanResult(failure=issue)
        return result
