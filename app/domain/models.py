"""Serializable contracts shared by data, rules, planner and agent modules."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from app.domain.cards import CookingStep, DishCard, RecipeProvenance
from app.nutrition.models import MenuNutrition, RecipeNutrition

DietMode = Literal["omnivore", "ovo_lacto_vegetarian", "vegan"]
MethodMealPriority = Literal["meal", "method"]
CookingMethod = Literal["蒸", "煮", "炖", "炒", "烤", "煎", "炸", "烧", "焖", "拌"]
MenuSlot = Annotated[int, Field(ge=1, le=8)]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("health_goals", mode="before", check_fields=False)
    @classmethod
    def normalize_declared_health_goal_names(cls, value: Any) -> Any:
        # Whole, explicitly supplied names only. This does not infer a goal or
        # diagnosis from age, symptoms, profile metrics, recipe labels or prose.
        # Preserve unknown names so their evidence gap stays visible. Non-list
        # or non-string inputs still pass to the existing strict validation.
        aliases = {
            "降血压": "降压",
            "控压": "降压",
            "控制血压": "降压",
            "控制血糖": "控糖",
            "减重": "减脂",
            "日常护心": "护心",
        }
        if isinstance(value, list):
            return [
                aliases.get(item, item) if isinstance(item, str) else item
                for item in value
            ]
        return value


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
    # Redacted source profiles omit anthropometry. Missing is not a normal
    # default, zero, or a BMI inferred from separately supplied measurements.
    height_cm: float | None = None
    weight_kg: float | None = None
    bmi: float | None = None
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
    allergy_clarifications: dict[str, list[str]] = Field(default_factory=dict)
    excluded_ingredients: list[str] = Field(default_factory=list, max_length=30)
    revoke_exclusions: list[str] = Field(default_factory=list, max_length=30)
    preferred_ingredients: list[str] = Field(default_factory=list, max_length=30)
    preferences: list[str] = Field(default_factory=list, max_length=30)
    health_goals: list[str] = Field(default_factory=list, max_length=30)
    no_spicy: bool | None = None
    diet_mode: DietMode | None = None


class Diner(DomainModel):
    """Stable participant identity and only the facts attributed to that person."""

    diner_id: str
    display_name: str = Field(min_length=1, max_length=50)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    attendance: bool = True
    profile_owner: bool = False
    # Legacy profile records do not prove which attendee owns the account.
    # attendance=True keeps their restrictions conservative until linked;
    # participation_basis decides whether they are a confirmed extra person.
    participation_basis: Literal["explicit", "profile_unlinked", "legacy"] = "legacy"
    allergies: list[str] = Field(default_factory=list)
    # Unknown terms stay attached to this identity until explicitly resolved.
    pending_allergy_terms: list[str] = Field(default_factory=list)
    pending_allergy: bool = False
    excluded_ingredients: list[str] = Field(default_factory=list)
    preferred_ingredients: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    health_goals: list[str] = Field(default_factory=list)
    no_spicy: bool = False
    diet_mode: DietMode = "omnivore"
    pending_diet_mode: bool = False


class ScopedMethod(DomainModel):
    """Grounded meal-local source-method target; never an inferred safety rule."""

    food: str = Field(min_length=1, max_length=30)
    method: CookingMethod
    slot: int | None = Field(default=None, ge=1, le=8)
    required: bool = True


class Constraints(DomainModel):
    allergies: list[str] = Field(default_factory=list)
    excluded_ingredients: list[str] = Field(default_factory=list)
    preferred_ingredients: list[str] = Field(default_factory=list)
    inventory: list[str] | None = None
    preferences: list[str] = Field(default_factory=list)
    scoped_methods: list[ScopedMethod] = Field(default_factory=list)
    # Meal-local preference exclusions, never allergies or profile facts.
    slot_food_exclusions: dict[MenuSlot, list[str]] = Field(default_factory=dict)
    health_goals: list[str] = Field(default_factory=list)
    no_spicy: bool = False
    diet_mode: DietMode = "omnivore"
    meal_type: str = "晚餐"
    dish_count: int = Field(default=3, ge=1, le=8)
    soup_count: int = Field(default=0, ge=0, le=3)
    # Literal meal-local allocation, never inferred from a profile/model label.
    # Dessert slots are included in dish_count, not extra dishes or entrées.
    dessert_count: int = Field(default=0, ge=0, le=3)
    meat_dish_count: int | None = Field(default=None, ge=0, le=8)
    vegetarian_dish_count: int | None = Field(default=None, ge=0, le=8)
    # Grounded meal-local quantity meaning, not an LLM- or profile-selected diet.
    # Old snapshots retain their declared animal-source culinary convention.
    meat_dish_scope: Literal["any_meat_source", "independent_entree"] = (
        "any_meat_source"
    )
    # Grounded source quantities apply only to soup slots, not the whole meal.
    meat_soup_count: int | None = Field(default=None, ge=0, le=3)
    vegetarian_soup_count: int | None = Field(default=None, ge=0, le=3)
    people: int = Field(default=1, ge=1, le=8)
    max_minutes: int | None = Field(default=None, ge=1, le=480)
    # Explicit current-context tradeoff only; never a safety override.
    method_meal_priority: MethodMealPriority | None = None


class RestoreConstraint(DomainModel):
    """A field-scoped request to restore an earlier explicit constraint value."""

    field: Literal["dish_count"]
    reference: Literal["original"] = "original"


class RestoreMenuIntent(DomainModel):
    """A menu-level request to restore an earlier menu snapshot (J18).

    The model only names which menu it wants; it never guesses recipe IDs. The
    resolver looks the menu up from recorded history and revalidates it.
    """

    reference: Literal["original"] = "original"


class Intent(DomainModel):
    # Adapter-only provenance. Not a JSON/model/persistence field or authority
    # the model/user can select: a guard must not erase an already parsed plan.
    _allergy_guard_plan: bool = PrivateAttr(default=False)
    # Only the finite pending-context answer guard can supply this proof.
    # It is not a JSON/model-selectable confirmation of missing restrictions.
    _context_answer_ack: bool = PrivateAttr(default=False)
    # Adapter-only bound replacement obligation after whole-menu consent.
    _replacement_exclusions: frozenset[str] = PrivateAttr(default_factory=frozenset)
    # Literal empty continuation of a completed legal menu: validate/report,
    # never call menu sorting. Not model-selectable or persisted authority.
    _retain_completed_menu: bool = PrivateAttr(default=False)
    _local_food_scopes: dict[int, list[str]] = PrivateAttr(default_factory=dict)
    action: Literal["plan", "replace", "reject", "explain", "clarify"] = "plan"
    excluded_ingredients: list[str] = Field(default_factory=list)
    revoke_exclusions: list[str] = Field(default_factory=list, max_length=30)
    revoke_confirmed: bool = False
    revoke_cancelled: bool = False
    allergies: list[str] = Field(default_factory=list)
    allergy_clarifications: dict[str, list[str]] = Field(default_factory=dict)
    diner_updates: list[DinerUpdate] = Field(default_factory=list, max_length=8)
    restore_constraints: list[RestoreConstraint] = Field(default_factory=list, max_length=4)
    restore_menu: RestoreMenuIntent | None = None
    preferred_ingredients: list[str] = Field(default_factory=list)
    health_goals: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    inventory: list[str] | None = None
    no_spicy: bool | None = None
    diet_mode: DietMode | None = None
    meal_type: str | None = None
    dish_count: int | None = Field(default=None, ge=1, le=8)
    soup_count: int | None = Field(default=None, ge=0, le=3)
    meat_dish_count: int | None = Field(default=None, ge=0, le=8)
    vegetarian_dish_count: int | None = Field(default=None, ge=0, le=8)
    people: int | None = Field(default=None, ge=1, le=8)
    restrictions_confirmed: bool = False
    max_minutes: int | None = Field(default=None, ge=1, le=480)
    clear_time_limit: bool = False
    replace_slot: int | None = Field(default=None, ge=1, le=8)
    replace_name: str | None = None
    replace_slots: list[MenuSlot] = Field(default_factory=list, max_length=8)
    keep_slots: list[MenuSlot] = Field(default_factory=list, max_length=8)
    local_excluded_ingredients: list[str] = Field(default_factory=list, max_length=30)
    query_terms: list[str] = Field(default_factory=list)
    clarification: str | None = None
    method_meal_priority: MethodMealPriority | None = None


class MethodMealTradeoff(DomainModel):
    binding_hash: str
    action: Literal["plan", "replace", "reject"]
    replace_slot: int | None = Field(default=None, ge=1, le=8)
    original_menu_ids: list[str]
    meal_option_ids: list[str]
    method_option_ids: list[str]
    prompt: str


class FlavorRetractionOption(DomainModel):
    display: str
    diner_id: str | None = None
    preference_index: int = Field(ge=0)
    before: str
    after: str
    removed_clause: str


class FlavorResolution(DomainModel):
    binding_hash: str
    action: Literal["plan", "replace", "reject", "explain"]
    replace_slot: int | None = Field(default=None, ge=1, le=8)
    original_menu_ids: list[str]
    conflicts: list[str]
    options: list[FlavorRetractionOption]
    prompt: str


class FlavorRetraction(DomainModel):
    owner: str
    before: str
    after: str
    removed_clause: str
    answer: str
    action: Literal["plan", "replace", "reject", "explain"]
    replace_slot: int | None = Field(default=None, ge=1, le=8)


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


class RecommendedMeal(DomainModel):
    """Source recommendation snapshot, not an eaten meal or health profile."""

    meal_type: str
    recipe_ids: list[str] = Field(max_length=8)
    recipe_names: list[str] = Field(max_length=8)


class ContextReplacementScope(DomainModel):
    """Retain local scope, even an unspecified target needing clarification.

    Legacy class/field names are kept for stored snapshots. Neither a missing
    index nor an ambiguous name grants whole-menu editing authority.
    """

    replace_slot: int | None = Field(default=None, ge=1, le=8)
    replace_name: str | None = None
    replace_slots: list[MenuSlot] = Field(default_factory=list, max_length=8)
    keep_slots: list[MenuSlot] = Field(default_factory=list, max_length=8)
    menu_ids: list[str] = Field(min_length=1, max_length=8)
    target_confirmed: bool = True
    whole_menu_authorized: bool = False

class PendingMenuCounts(DomainModel):
    """A well-formed request awaiting correction, never effective constraints."""

    dish_count: int = Field(ge=1, le=8)
    soup_count: int = Field(ge=0, le=3)


class PendingRevokeExclusion(DomainModel):
    """A revocation request awaiting confirmation, ordinary exclusions only."""

    subject: str | None = None  # None = the speaker's meal-level exclusions
    targets: list[str] = Field(default_factory=list, max_length=30)


class ConstraintRevision(DomainModel):
    """One explicitly confirmed value of a restorable constraint field."""

    field: str
    value: int
    turn_index: int
    source: Literal["explicit_user"] = "explicit_user"


class MenuRevision(DomainModel):
    """One valid menu version actually generated and returned to the user."""

    revision_id: str
    turn_index: int
    recipe_ids: list[str]
    source: Literal["planned_menu", "restored_menu"] = "planned_menu"


class RejectionAction(DomainModel):
    """One whole-menu rejection, recording only the newly rejected delta."""

    action_id: str
    turn_index: int
    rejected_recipe_ids: list[str]
    source_menu_revision_id: str | None = None
    active: bool = True


class SessionState(DomainModel):
    # Store-owned optimistic history token, never selectable by model JSON.
    _recommendation_history_revision: int | None = PrivateAttr(default=None)
    session_id: str
    user_id: int
    revision: int = 0
    constraints: Constraints = Field(default_factory=Constraints)
    meal_constraints: Constraints | None = None
    diners: list[Diner] = Field(default_factory=list, max_length=8)
    menu_structure_explicit: bool = False
    menu_ids: list[str] = Field(default_factory=list)
    # Server-owned records: explicit local persistence, not public API state or
    # model context. User/request schemas cannot supply generated recipes.
    generated_recipes: dict[str, Recipe] = Field(default_factory=dict, exclude=True)
    meal_sequence: int = Field(default=0, ge=0)
    recent_recommendations: list[RecommendedMeal] = Field(
        default_factory=list, max_length=8
    )
    last_recommendation: RecommendedMeal | None = None
    last_recommendation_sequence: int | None = Field(default=None, ge=0)
    # Explicitly rejected dishes remain excluded for this single-meal session.
    # Ordinary local replacement does not create a lasting food restriction.
    rejected_recipe_ids: list[str] = Field(default_factory=list)
    menu_valid: bool = False
    pending_allergy: bool = False
    pending_allergy_terms: list[str] = Field(default_factory=list)
    pending_clarification: str | None = None
    # An allergy answer may complete an already requested, unfinished meal plan.
    pending_plan: bool = False
    pending_diet_mode: bool = False
    pending_dish_composition: bool = False
    pending_soup_composition: bool = False
    pending_dessert_allocation: bool = False
    pending_method_tradeoff: MethodMealTradeoff | None = None
    method_meal_priority_binding: str | None = None
    pending_flavor_resolution: FlavorResolution | None = None
    pending_context_replacement: ContextReplacementScope | None = None
    last_flavor_retraction: FlavorRetraction | None = None
    flavor_retractions: list[FlavorRetraction] = Field(default_factory=list)
    # Literal, source-scoped session amendments; never model-selected removals.
    last_scene_retractions: list[dict[str, str]] = Field(default_factory=list)
    scene_retractions: list[dict[str, str]] = Field(default_factory=list)
    last_direct_flavor_retractions: list[dict[str, str]] = Field(default_factory=list)
    direct_flavor_retractions: list[dict[str, str]] = Field(default_factory=list)

    pending_menu_counts: PendingMenuCounts | None = None
    pending_revoke_exclusion: PendingRevokeExclusion | None = None
    last_message: str = ""
    history: list[dict[str, str]] = Field(default_factory=list)
    constraint_history: list[ConstraintRevision] = Field(default_factory=list)
    # Menu version snapshots and per-rejection deltas support scoped restore
    # (J18). They are separate from J17's field-scoped constraint_history.
    menu_history: list[MenuRevision] = Field(default_factory=list)
    rejection_actions: list[RejectionAction] = Field(default_factory=list)
    confirmed_fields: list[Literal["people", "meal_type", "restrictions"]] = Field(
        default_factory=list
    )
    pending_fields: list[
        Literal[
            "people",
            "meal_type",
            "restrictions",
            "allergy",
            "time_limit",
            "request",
            "method_meal_priority",
            "flavor_resolution",
        ]
    ] = Field(default_factory=list)


class ClarificationQuestion(DomainModel):
    field: Literal[
        "people",
        "meal_type",
        "restrictions",
        "allergy",
        "time_limit",
        "request",
        "method_meal_priority",
        "flavor_resolution",
    ]
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
