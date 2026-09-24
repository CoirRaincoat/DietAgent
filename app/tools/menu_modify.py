"""Shared initial planning and replacement tool with constraint enforcement."""

from app.agent.planner import MenuPlanner, PlanResult
from app.domain.models import Constraints, Recipe


class MenuModifyTool:
    def __init__(self, planner: MenuPlanner):
        self.planner = planner

    def __call__(
        self, candidates: list[Recipe], constraints: Constraints,
        current: list[Recipe] | None = None, replace_slot: int | None = None,
        reject_ids: set[str] | None = None,
    ) -> PlanResult:
        return self.planner.plan(candidates, constraints, current, replace_slot, reject_ids)
