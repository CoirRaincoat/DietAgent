"""Presentation contracts for qualitative, ingredient-traceable nutrition."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NutritionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngredientContribution(NutritionModel):
    recipe_id: str
    source_row: int
    ingredient_name: str
    roles: list[Literal["protein", "carbohydrate", "fat", "dietary_fiber"]]
    explanation: str
    quantity_recorded: bool


class NutritionSource(NutritionModel):
    source_id: str
    title: str
    url: str


class GoalMatch(NutritionModel):
    goal: str
    status: Literal["preference_match", "caution", "insufficient_data"]
    ingredient_names: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    limitation: str
    sources: list[NutritionSource] = Field(default_factory=list)


class NutritionRisk(NutritionModel):
    code: str
    message: str
    ingredient_names: list[str] = Field(default_factory=list)
    recipe_ids: list[str] = Field(default_factory=list)


class RecipeNutrition(NutritionModel):
    recipe_id: str
    source_row: int
    protein_sources: list[str] = Field(default_factory=list)
    carbohydrate_sources: list[str] = Field(default_factory=list)
    fat_sources: list[str] = Field(default_factory=list)
    dietary_fiber: list[str] = Field(default_factory=list)
    ingredient_contributions: list[IngredientContribution] = Field(default_factory=list)
    goal_matches: list[GoalMatch] = Field(default_factory=list)
    suitable_reasons: list[str] = Field(default_factory=list)
    risks: list[NutritionRisk] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    analysis_type: Literal["qualitative"] = "qualitative"


class MenuNutrition(NutritionModel):
    recipe_ids: list[str] = Field(default_factory=list)
    protein_sources: list[str] = Field(default_factory=list)
    carbohydrate_sources: list[str] = Field(default_factory=list)
    fat_sources: list[str] = Field(default_factory=list)
    dietary_fiber: list[str] = Field(default_factory=list)
    ingredient_contributions: list[IngredientContribution] = Field(default_factory=list)
    goal_matches: list[GoalMatch] = Field(default_factory=list)
    suitable_reasons: list[str] = Field(default_factory=list)
    risks: list[NutritionRisk] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    recipe_analyses: list[RecipeNutrition] = Field(default_factory=list)
    analysis_type: Literal["qualitative"] = "qualitative"
