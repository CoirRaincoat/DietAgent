// Transport types mirror backend schema_version=2.0; no frontend nutrient estimates.
export interface Ingredient { raw: string; name: string; quantity: number | null; unit: string | null }
export interface NutritionSource { source_id: string; title: string; url: string }
export interface IngredientContribution { recipe_id: string; source_row: number; ingredient_name: string; roles: ('protein' | 'carbohydrate' | 'fat' | 'dietary_fiber')[]; explanation: string; quantity_recorded: boolean }
export interface GoalMatch { goal: string; status: 'preference_match' | 'caution' | 'insufficient_data'; ingredient_names: string[]; methods: string[]; reasons: string[]; limitation: string; sources: NutritionSource[] }
export interface NutritionRisk { code: string; message: string; ingredient_names: string[]; recipe_ids: string[] }
export interface NutritionBase {
  protein_sources: string[]; carbohydrate_sources: string[]; fat_sources: string[]; dietary_fiber: string[];
  ingredient_contributions: IngredientContribution[]; goal_matches: GoalMatch[];
  suitable_reasons: string[]; risks: NutritionRisk[]; limitations: string[]; analysis_type: 'qualitative';
}
export interface RecipeNutrition extends NutritionBase { recipe_id: string; source_row: number }
export interface MenuNutrition extends NutritionBase { recipe_ids: string[]; recipe_analyses: RecipeNutrition[] }
export interface MenuItem {
  slot: number; recipe_id: string; name: string; ingredients: string[]; steps: string;
  reasons: string[]; nutrition_notes: string[]; source: string;
  card: { title: string; subtitle: string; badges: string[]; image_url: null; cooking_minutes: null; servings: null } | null;
  ingredient_details: Ingredient[]; cooking_steps: {number: number; description: string}[];
  provenance: {recipe_id: string; source_row: number; fingerprint: string} | null;
  nutrition: RecipeNutrition | null; replacement_reason: string | null;
}
export interface Constraints {
  allergies: string[]; excluded_ingredients: string[]; preferred_ingredients: string[]; inventory: string[] | null;
  preferences: string[]; health_goals: string[]; no_spicy: boolean; meal_type: string;
  dish_count: number; soup_count: number; people: number; max_minutes: number | null;
}
export interface Diner {
  diner_id: string; display_name: string; aliases: string[]; attendance: boolean; profile_owner: boolean;
  allergies: string[]; excluded_ingredients: string[]; preferred_ingredients: string[];
  preferences: string[]; health_goals: string[]; no_spicy: boolean;
}
export interface DinerSuitability {
  diner_id: string; display_name: string; hard_constraints_satisfied: boolean;
  known_constraints: string[]; violations: string[]; unmet_preferences: string[]; scope_note: string;
}
export interface SessionState {
  session_id: string; user_id: number; revision: number; constraints: Constraints;
  meal_constraints: Constraints | null; diners: Diner[]; menu_structure_explicit: boolean;
  menu_ids: string[]; menu_valid: boolean; pending_allergy: boolean; pending_allergy_terms: string[];
  pending_clarification: string | null; last_message: string; history: {role:string;content:string}[];
  confirmed_fields: string[]; pending_fields: string[];
}
export interface ClarificationQuestion { field: string; prompt: string; options: string[] }
export interface ChatResult {
  schema_version: '2.0'; status: 'ok' | 'clarification_required' | 'no_feasible_menu';
  menu: MenuItem[]; reason: string; constraints: string[]; conversation_state: SessionState;
  replacement_suggestions: MenuItem[]; warnings: string[];
  tool_calls: {name: string; summary: Record<string, unknown>}[]; timings_ms: Record<string, number>;
  explanation_source: string; clarification_questions: ClarificationQuestion[]; nutrition_analysis: MenuNutrition | null;
  diner_suitability: DinerSuitability[];
}
export interface ChatRequest { user_id: number; message: string; request_id: string; session_id?: string }
export interface DemoProfile { user_id: number; label: string; allergies: string[]; health_goals: string[]; preferences: string[] }
export interface Health { status: string; recipe_count: number; profile_count: number; profile_data_scope: string[]; llm_configured: boolean; tools: string[]; model: string }
export interface Message { id: string; role: 'user' | 'assistant'; text: string; failed?: boolean; response?: ChatResult }
