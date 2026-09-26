"""Serializable contracts shared by data, rules, planner and agent modules."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.cards import CookingStep, DishCard, RecipeProvenance
from app.nutrition.models import MenuNutrition, RecipeNutrition


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ingredient(DomainModel):
    raw: str
    name: str
    quantity: float | None = None
    unit: str | None = None


class Recipe(DomainModel):
    recipe_id: str
    name: str
    raw_ingredients: str
    steps: str
    raw_label: str = ""
    ingredients: list[Ingredient] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    meal_types: list[str] = Field(default_factory=list)
    source_row: int
    fingerprint: str
    eligible: bool = True
    quality_flags: list[str] = Field(default_factory=list)


class UserProfile(DomainModel):
    data_scope: Literal["original", "synthetic"] = "original"
    user_id: int
    age: int
    sex: str
    height_cm: float
    weight_kg: float
    bmi: float
    activity: str = ""
    special_groups: list[str] = Field(default_factory=list)
    pregnancy_weeks: int | None = None
    preferences: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    health_goals: list[str] = Field(default_factory=list)
    measurements: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class DinerUpdate(DomainModel):
    """One explicitly attributed participant update extracted from a turn."""

    diner: str = Field(min_length=1, max_length=50)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    attendance: bool | None = None
    allergies: list[str] = Field(default_factory=list, max_length=30)
    excluded_ingredients: list[str] = Field(default_factory=list, max_length=30)
    preferred_ingredients: list[str] = Field(default_factory=list, max_length=30)
    preferences: list[str] = Field(default_factory=list, max_length=30)
    health_goals: list[str] = Field(default_factory=list, max_length=30)
    no_spicy: bool | None = None


class Diner(DomainModel):
    """Stable participant identity and only the facts attributed to that person."""

    diner_id: str
    display_name: str = Field(min_length=1, max_length=50)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    attendance: bool = True
    profile_owner: bool = False
    allergies: list[str] = Field(default_factory=list)
    excluded_ingredients: list[str] = Field(default_factory=list)
    preferred_ingredients: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    health_goals: list[str] = Field(default_factory=list)
    no_spicy: bool = False


class Constraints(DomainModel):
    allergies: list[str] = Field(default_factory=list)
    excluded_ingredients: list[str] = Field(default_factory=list)
    preferred_ingredients: list[str] = Field(default_factory=list)
    inventory: list[str] | None = None
    preferences: list[str] = Field(default_factory=list)
    health_goals: list[str] = Field(default_factory=list)
    no_spicy: bool = False
    meal_type: str = "晚餐"
    dish_count: int = Field(default=3, ge=1, le=8)
    soup_count: int = Field(default=0, ge=0, le=3)
    people: int = Field(default=1, ge=1, le=8)
    max_minutes: int | None = Field(default=None, ge=1, le=480)


class Intent(DomainModel):
    action: Literal["plan", "replace", "reject", "explain", "clarify"] = "plan"
    excluded_ingredients: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    allergy_clarifications: dict[str, list[str]] = Field(default_factory=dict)
    diner_updates: list[DinerUpdate] = Field(default_factory=list, max_length=8)
    preferred_ingredients: list[str] = Field(default_factory=list)
    health_goals: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    inventory: list[str] | None = None
    no_spicy: bool | None = None
    meal_type: str | None = None
    dish_count: int | None = Field(default=None, ge=1, le=8)
    soup_count: int | None = Field(default=None, ge=0, le=3)
    people: int | None = Field(default=None, ge=1, le=8)
    restrictions_confirmed: bool = False
    max_minutes: int | None = Field(default=None, ge=1, le=480)
    clear_time_limit: bool = False
    replace_slot: int | None = Field(default=None, ge=1, le=8)
    replace_name: str | None = None
    query_terms: list[str] = Field(default_factory=list)
    clarification: str | None = None


class MenuItem(DomainModel):
    slot: int
    recipe_id: str
    name: str
    ingredients: list[str]
    steps: str
    reasons: list[str] = Field(default_factory=list)
    nutrition_notes: list[str] = Field(default_factory=list)
    source: str = "方太菜谱库"
    card: DishCard | None = None
    ingredient_details: list[Ingredient] = Field(default_factory=list)
    cooking_steps: list[CookingStep] = Field(default_factory=list)
    provenance: RecipeProvenance | None = None
    nutrition: RecipeNutrition | None = None
    replacement_reason: str | None = None


class SessionState(DomainModel):
    session_id: str
    user_id: int
    revision: int = 0
    constraints: Constraints = Field(default_factory=Constraints)
    meal_constraints: Constraints | None = None
    diners: list[Diner] = Field(default_factory=list, max_length=8)
    menu_structure_explicit: bool = False
    menu_ids: list[str] = Field(default_factory=list)
    # Explicitly rejected dishes remain excluded for this single-meal session.
    # Ordinary local replacement does not create a lasting food restriction.
    rejected_recipe_ids: list[str] = Field(default_factory=list)
    menu_valid: bool = False
    pending_allergy: bool = False
    pending_allergy_terms: list[str] = Field(default_factory=list)
    pending_clarification: str | None = None
    last_message: str = ""
    history: list[dict[str, str]] = Field(default_factory=list)
    confirmed_fields: list[Literal["people", "meal_type", "restrictions"]] = Field(
        default_factory=list
    )
    pending_fields: list[Literal[
        "people", "meal_type", "restrictions", "allergy", "time_limit", "request"
    ]] = Field(default_factory=list)


class ClarificationQuestion(DomainModel):
    field: Literal["people", "meal_type", "restrictions", "allergy", "time_limit", "request"]
    prompt: str
    options: list[str] = Field(default_factory=list)


class DinerSuitability(DomainModel):
    """Per-person validation summary over known facts only."""

    diner_id: str
    display_name: str
    hard_constraints_satisfied: bool
    known_constraints: list[str] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    unmet_preferences: list[str] = Field(default_factory=list)
    scope_note: str



class ToolEvent(DomainModel):
    name: str
    summary: dict[str, Any] = Field(default_factory=dict)


class ChatResult(DomainModel):
    schema_version: Literal["2.0"] = "2.0"
    status: Literal["ok", "clarification_required", "no_feasible_menu"]
    menu: list[MenuItem] = Field(default_factory=list)
    reason: str
    constraints: list[str] = Field(default_factory=list)
    conversation_state: SessionState
    replacement_suggestions: list[MenuItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    tool_calls: list[ToolEvent] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    explanation_source: str = "verified_template"
    clarification_questions: list[ClarificationQuestion] = Field(default_factory=list)
    nutrition_analysis: MenuNutrition | None = None
    diner_suitability: list[DinerSuitability] = Field(default_factory=list)
