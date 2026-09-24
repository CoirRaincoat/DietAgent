"""Deterministic constraint checking is a tool, not a model verdict."""

from app.domain.models import Constraints, Recipe
from app.rules.engine import RuleDecision, RuleEngine


class HealthCheckTool:
    def __init__(self, rules: RuleEngine):
        self.rules = rules

    def evaluate(self, recipe: Recipe, constraints: Constraints) -> RuleDecision:
        return self.rules.evaluate(recipe, constraints)

    def __call__(self, recipes: list[Recipe], constraints: Constraints) -> list[Recipe]:
        return [recipe for recipe in recipes if self.evaluate(recipe, constraints).allowed]
