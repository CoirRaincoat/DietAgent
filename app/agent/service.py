"""Stateful retrieval-grounded meal planning with independently validated output."""

import asyncio
import hashlib
import re
from time import perf_counter
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.agent.clarification import (
    allergy_answer_only,
    confirm_from_intent,
    explicit_allergy_resolution,
    explicit_self_allergy_facts,
    missing_questions,
    recover_explicit_meal_context,
)
from app.agent.context_answers import grounded_context_answer
from app.agent.context_retry_scope import preserve_context_retry_scope, resolve_replacement_target
from app.agent.count_conflicts import apply_menu_counts
from app.agent.dessert_requests import explicit_dessert_count
from app.agent.diet_mode import DIET_QUESTION, ground_diet_intent
from app.agent.diners import (
    DinerConflict,
    aggregate_constraints,
    apply_diner_updates,
    diner_suitability,
    effective_attendee_count,
    find_diner,
    is_unlinked_profile,
    profile_diner,
)
from app.agent.dish_composition import explicit_dish_composition, independent_meat_requested
from app.agent.flavor_resolution import (
    binding as flavor_resolution_binding,
)
from app.agent.flavor_resolution import (
    choice as flavor_resolution_choice,
)
from app.agent.flavor_resolution import (
    make_pending as make_flavor_resolution,
)
from app.agent.flavor_resolution import (
    retract as retract_flavor_clause,
)
from app.agent.flavor_resolution import (
    retraction_copy,
)
from app.agent.flavor_withdrawal import apply_flavor_withdrawals, direct_flavor_retraction_copy
from app.agent.food_exclusions import explicit_food_exclusions, explicit_query_food_preferences
from app.agent.history_restore import apply_constraint_restore
from app.agent.menu_balance import analyze_menu_balance
from app.agent.menu_restore import apply_menu_restore, record_menu_revision, record_rejection_action
from app.agent.menu_structure import explicit_menu_structure, explicit_soup_composition
from app.agent.method_meal_tradeoff import (
    OPTIONS as METHOD_MEAL_OPTIONS,
)
from app.agent.method_meal_tradeoff import (
    find_tradeoff,
    literal_priority_choice,
    make_pending,
    pending_binding,
    priority_binding,
)
from app.agent.planner import MenuPlanner, PlanResult
from app.agent.recipe_generation import (
    propose_missing_liangfen,
    propose_missing_tofu,
    session_recipe_records,
    verified_generated_recipe,
)
from app.agent.request_authority import empty_continuation_request, menu_read_only_request
from app.agent.response_copy import (
    clarification_copy,
    required_fact_ids,
    response_facts,
    state_change_facts,
)
from app.agent.revoke_exclusion import apply_revoke_exclusion
from app.agent.scene_retraction import apply_scene_withdrawals, scene_retraction_copy
from app.agent.suggestions import replacement_candidates
from app.api.presentation import build_card, recipe_provenance, split_cooking_steps
from app.domain.advance_preparation import advance_preparation_copy
from app.domain.allergy_mentions import repair_proven_reference_pending, uncovered_allergy_mentions
from app.domain.context_exclusions import context_conflict_issue
from app.domain.dining_scenes import scene_request_clauses
from app.domain.dish_composition import composition_issue, composition_satisfied
from app.domain.entree_preferences import entree_request_clauses
from app.domain.generated_recipe import normalize_proposal, verified_proposal
from app.domain.matching_tags import (
    explicit_non_spicy_flavor_preference,
    flavor_conflicts,
    negative_flavor_request_clauses,
    supported_flavor_exclusions,
    supported_flavor_preferences,
)
from app.domain.meal_context import negative_meal_request_clauses
from app.domain.meal_history import HISTORY_LIMIT, explicit_new_meal
from app.domain.meal_roles import dessert_structure_satisfied, is_menu_recipe
from app.domain.method_preferences import method_request_clauses
from app.domain.models import (
    ChatResult,
    ClarificationQuestion,
    Constraints,
    ContextReplacementScope,
    Diner,
    DinerUpdate,
    Intent,
    MenuItem,
    Recipe,
    RecommendedMeal,
    SessionState,
    ToolEvent,
    UserProfile,
)
from app.domain.recipe_origin import generator_version
from app.domain.scoped_methods import (
    amend_scoped_methods,
    explicit_scoped_methods,
    missing_scoped_methods,
    scoped_method_mask,
)
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM, LLMOutputError, LLMUnavailable
from app.infrastructure.sessions import SessionStore
from app.retrieval.core import RecipeRetriever
from app.retrieval.keyword import KeywordRetriever
from app.rules.diet import diet_issue
from app.rules.engine import RuleEngine, compact
from app.tools.health_check import HealthCheckTool
from app.tools.menu_modify import MenuModifyTool
from app.tools.nutrition_analysis import NutritionAnalysisTool
from app.tools.recipe_search import RecipeSearchTool
from app.tools.registry import ToolRegistry


class UnknownUser(Exception):
    pass


class UnknownSession(Exception):
    pass


def _merge(current: list[str], added: list[str]) -> list[str]:
    return list(dict.fromkeys(current + [item.strip() for item in added if item.strip()]))


class MealAgent:
    def __init__(self, catalog: DataCatalog, store: SessionStore, llm: BaseLLM, *,
                 experiment_cross_meal_rotation: bool = False,
                 allow_recipe_generation: bool = False,
                 retriever: RecipeRetriever | None = None):
        self.catalog = catalog
        self.store = store
        self.llm = llm
        self.allow_recipe_generation = allow_recipe_generation
        # API delivery defaults opt in after frozen M02 source replay. Direct
        # callers retain the compatibility switch; HTTP/model payloads cannot
        # choose it or confer a new-meal boundary without literal user authority.
        self.experiment_cross_meal_rotation = experiment_cross_meal_rotation
        self.rules = RuleEngine()
        self.retriever = retriever if retriever is not None else KeywordRetriever(catalog.recipes.values())
        self.planner = MenuPlanner(self.rules)
        self._locks: dict[str, asyncio.Lock] = {}
        self._user_locks: dict[int, asyncio.Lock] = {}
        self.health_tool = HealthCheckTool(self.rules)
        self.tools = ToolRegistry()
        self.tools.register("recipe_search", RecipeSearchTool(self.retriever))
        self.tools.register("health_check", self.health_tool)
        self.tools.register("menu_modify", MenuModifyTool(self.planner))
        self.tools.register("nutrition_analysis", NutritionAnalysisTool())

    def _recipes(self, state: SessionState) -> dict[str, Recipe]:
        return session_recipe_records(self.catalog.recipes, state)

    async def chat(
        self, user_id: int, message: str, session_id: str | None = None,
        request_id: str | None = None,
    ) -> ChatResult:
        if user_id not in self.catalog.profiles:
            raise UnknownUser("用户档案不存在。")
        supplied_session = session_id is not None
        session_id = session_id or (
            uuid5(NAMESPACE_URL, f"fangtai:{user_id}:{request_id}").hex
            if request_id else uuid4().hex
        )
        request_hash = hashlib.sha256(f"{user_id}\n{message}".encode()).hexdigest()
        lock = (
            self._user_locks.setdefault(user_id, asyncio.Lock())
            if self.experiment_cross_meal_rotation
            else self._locks.setdefault(session_id, asyncio.Lock())
        )
        async with lock:
            state = self.store.get(session_id, user_id)
            if state is None and supplied_session:
                raise UnknownSession("会话不存在；首次请求请省略 session_id。")
            if state and request_id:
                cached = self.store.replay(session_id, request_id, request_hash, state.revision)
                if cached:
                    return cached
            profile = self.catalog.profiles[user_id]
            if state is None:
                diners = [profile_diner(profile)]
                meal_constraints = Constraints()
                state = SessionState(
                    session_id=session_id, user_id=user_id,
                    confirmed_fields=["restrictions"] if profile.allergies else [],
                    constraints=aggregate_constraints(meal_constraints, diners),
                    meal_constraints=meal_constraints,
                    diners=diners,
                )
                expected_revision = None
            else:
                self._ensure_diner_state(state, profile)
                expected_revision = state.revision
            started = perf_counter()
            read_only_authority = menu_read_only_request(message)
            state.last_flavor_retraction = None
            state.last_scene_retractions = []
            state.last_direct_flavor_retractions = []
            pending_flavor = state.pending_flavor_resolution
            if (
                pending_flavor is not None
                and pending_flavor.action == "plan"
                and pending_flavor.replace_slot is None
                and not state.menu_ids
                and state.pending_plan
                and read_only_authority is None
                and empty_continuation_request(message)
                and not flavor_conflicts(state.constraints.preferences)
            ):
                # An old false initial conflict may disappear after a polarity
                # correction. Resume only its unfulfilled initial plan; do not
                # retract a source, touch read-only requests or broaden edits.
                state.pending_flavor_resolution = None
                pending_flavor = None
            flavor_option = flavor_resolution_choice(message, pending_flavor)
            flavor_resolution_answer = False
            if read_only_authority is not None:
                # The literal user's scope, not a misclassified model action,
                # is authoritative before rejection/history/pending consumers.
                intent = await self.llm.parse(message, state, profile)
            elif flavor_option is not None and pending_flavor is not None:
                context_matches = pending_flavor.binding_hash == flavor_resolution_binding(
                    state, list(self._recipes(state).values()), pending_flavor.action, pending_flavor.replace_slot,
                )
                fresh = make_flavor_resolution(state, list(self._recipes(state).values()),
                    pending_flavor.action, pending_flavor.replace_slot)
                if context_matches and flavor_option in fresh.options:
                    retract_flavor_clause(state, pending_flavor, flavor_option, message)
                    intent = Intent(action=pending_flavor.action, replace_slot=pending_flavor.replace_slot)
                    flavor_resolution_answer = True
                else:
                    state.pending_flavor_resolution = None
                    intent = Intent(action="clarify", clarification="旧口味撤回选项已失效；当前来源或要求已变化，未撤回任何内容，请重新核对本餐。")
            elif pending_flavor is not None and message.strip() == "暂不规划":
                state.pending_plan = False
                intent = Intent(action="clarify", clarification="本轮暂不规划，未撤回任何口味或安全要求；已保留原任务范围。")
            elif message.strip().startswith("撤回"):
                intent = Intent(action="clarify", clarification="请明确回复当前显示的完整撤回选项；孤立、旧选项、问题或混合命令不解除要求。")
            elif pending_flavor is not None and re.fullmatch(
                r"(?:继续|随便|(?:保留|取消|选择|选)(?:不?)(?:酸甜|酸|甜|蒜香|清淡)(?:口味|偏好|要求)?|第[一二三四五六七八九十0-9]+个)[。.!！]?",
                message.strip(),
            ):
                intent = Intent(action="clarify", clarification=pending_flavor.prompt)
            else:
                intent = await self.llm.parse(message, state, profile)
            context_answer = grounded_context_answer(message, state)
            if context_answer is not None and read_only_authority is None:
                # Reply to this pending initial plan, not model-selected edit
                # authority. The finite whole reply contains context only.
                intent = context_answer
            if pending_flavor is None and empty_continuation_request(message):
                # A finite literal continuation is no new fact/edit authority,
                # even if extraction echoes a profile or fabricates a rejection.
                # Bound pending flavor replies above keep their original path.
                # With an already valid menu and no outstanding plan/edit,
                # continuation is presentation, not soft-score replanning.
                has_completed_menu = (
                    state.menu_valid and bool(state.menu_ids)
                    and not state.pending_plan
                    and state.pending_context_replacement is None
                    and state.pending_method_tradeoff is None
                )
                intent = Intent(action="plan")
                intent._retain_completed_menu = has_completed_menu
            if intent.action == "explain" or read_only_authority is not None:
                # Parsed descriptive fields are not amendment authority. Strip
                # them before priority handling and any state-changing consumer.
                # Independently grounded safety assertions are checked below.
                intent = self._read_only_intent(state, intent, message)
            if read_only_authority is None:
                intent = preserve_context_retry_scope(state, intent, message)
            pending_tradeoff = state.pending_method_tradeoff
            priority_choice = literal_priority_choice(message)
            priority_answer = False
            catalog_records = list(self._recipes(state).values())
            if priority_choice in {"meal", "method"}:
                if pending_tradeoff is not None and pending_tradeoff.binding_hash == pending_binding(
                    state.constraints, catalog_records, state.menu_ids,
                    pending_tradeoff.action, pending_tradeoff.replace_slot,
                ):
                    # Literal answer to this exact persisted question resumes
                    # its original scope even if the model returned clarify.
                    intent = Intent(action=pending_tradeoff.action,
                                    replace_slot=pending_tradeoff.replace_slot,
                                    method_meal_priority=priority_choice)
                    state.method_meal_priority_binding = priority_binding(state.constraints, catalog_records)
                    state.pending_method_tradeoff = None
                    priority_answer = True
                else:
                    state.pending_method_tradeoff = None
                    intent = Intent(action="clarify", clarification="旧优先项问题未核对或已失效，请重新核对本餐；不能从孤立选项更改优先级。")
            elif pending_tradeoff is not None and priority_choice == "pause":
                state.pending_plan = False
                intent = Intent(action="clarify", clarification="本轮暂不规划；已保留要求和原换菜范围，未选择任何优先项。")
            elif intent.method_meal_priority is not None:
                intent = intent.model_copy(update={"method_meal_priority": None,
                    "action": "clarify", "clarification": "请先核对餐次／明确做法取舍，并明确回复显示的优先项；不采用模型自行选择。"})
            if not priority_answer and intent.action in {"replace", "reject"}:
                # New edit/reject authority is not an extension of an old
                # local tradeoff. Reconfirm if the new bounded scope needs it.
                state.pending_method_tradeoff = None
                state.method_meal_priority_binding = None
                state.constraints.method_meal_priority = None
                if state.meal_constraints is not None:
                    state.meal_constraints.method_meal_priority = None
                if not flavor_resolution_answer:
                    state.pending_flavor_resolution = None
            if intent.action in {"plan", "replace", "reject"} and not flavor_resolution_answer:
                # Only finite explicit scene/method clauses supplement extraction;
                # a question/explanation never authorizes a menu recheck.
                scene_clauses = scene_request_clauses(message)
                meal_exclusions = negative_meal_request_clauses(message)
                if intent.meal_type is not None and "不要" + intent.meal_type in meal_exclusions:
                    # A negative meal mention does not authorize switching into it.
                    intent = intent.model_copy(update={"meal_type": None})
                method_clauses = method_request_clauses(message)
                entree_clauses = entree_request_clauses(message)
                negative_flavor_clauses = negative_flavor_request_clauses(message)
                # Strict non-spicy phrases already have an independent hard
                # field. Do not duplicate them as a newly unknown soft flavor.
                strict_non_spicy = explicit_non_spicy_flavor_preference(negative_flavor_clauses)
                negative_flavor_clauses = tuple(
                    clause for clause in negative_flavor_clauses
                    if not (
                        explicit_non_spicy_flavor_preference((clause,))
                        and not supported_flavor_preferences((clause,))
                        and set(supported_flavor_exclusions((clause,))) == {"辣"}
                    )
                )
                if scene_clauses or meal_exclusions or method_clauses or negative_flavor_clauses or entree_clauses:
                    intent = intent.model_copy(update={
                        "preferences": _merge(intent.preferences, [*scene_clauses, *meal_exclusions, *method_clauses,
                                                                   *negative_flavor_clauses, *entree_clauses])
                    })
                if strict_non_spicy:
                    intent = intent.model_copy(update={"no_spicy": True})
            if read_only_authority is None and intent.action in {"plan", "replace", "reject"}:
                owner_exclusions = {
                    self.rules.canonical_food(food) for diner in state.diners
                    if diner.profile_owner for food in diner.excluded_ingredients
                }
                for update in intent.diner_updates:
                    try:
                        owner = find_diner(state.diners, update)
                    except DinerConflict:
                        # Leave identity conflicts to the existing validated
                        # update consumer; never guess or raise before it.
                        owner = None
                    if owner is not None and owner.profile_owner:
                        owner_exclusions.update(self.rules.canonical_food(food)
                                                for food in update.excluded_ingredients)
                literal_exclusions = explicit_food_exclusions(
                    message,
                    lambda food: not self.rules.unresolved_exclusions(
                        Constraints(excluded_ingredients=[food]),
                    ),
                    whole_meal_only=intent.action == "replace",
                    self_recorded=lambda food: self.rules.canonical_food(food) in owner_exclusions,
                )
                excluded = list(intent.excluded_ingredients)
                known = {self.rules.canonical_food(food) for food in excluded}
                if state.meal_constraints is not None:
                    known.update(self.rules.canonical_food(food)
                                 for food in state.meal_constraints.excluded_ingredients)
                for food in literal_exclusions:
                    canonical = self.rules.canonical_food(food)
                    if canonical not in known:
                        excluded.append(food)
                        known.add(canonical)
                if excluded != intent.excluded_ingredients:
                    intent = intent.model_copy(update={"excluded_ingredients": excluded})
            if read_only_authority is None and intent.action in {"plan", "reject"}:
                # Retrieval keywords do not persist themselves. Only a complete
                # current whole-meal want grounds a query-only food preference;
                # neither a local edit nor an explanation grants that authority.
                literal_preferences = explicit_query_food_preferences(message, intent.query_terms)
                preferred = list(intent.preferred_ingredients)
                known_preferences = {self.rules.canonical_food(food) for food in preferred}
                if state.meal_constraints is not None:
                    known_preferences.update(self.rules.canonical_food(food)
                                             for food in state.meal_constraints.preferred_ingredients)
                for food in literal_preferences:
                    canonical = self.rules.canonical_food(food)
                    if canonical not in known_preferences:
                        preferred.append(food)
                        known_preferences.add(canonical)
                if preferred != intent.preferred_ingredients:
                    intent = intent.model_copy(update={"preferred_ingredients": preferred})
            if (
                read_only_authority is None
                and intent.action in {"plan", "replace", "reject", "clarify"}
                and not (
                    state.pending_plan
                    and set(state.pending_fields) & {"people", "meal_type", "restrictions"}
                    and context_answer is None
                )
            ):
                intent = recover_explicit_meal_context(intent, message)
            parsed = perf_counter()
            new_meal_boundary = (
                self.experiment_cross_meal_rotation and intent.action == "plan"
                and explicit_new_meal(message) and not priority_answer
                and not flavor_resolution_answer
            )
            if new_meal_boundary:
                # Preserve all requirements/diners and unknown safety questions.
                # A failed/rejected planning attempt must not erase the last
                # returned recommendation. Legacy snapshots are not backfilled
                # into user history, but a still-valid current menu may migrate
                # into the session-local archive at this explicit boundary.
                if state.last_recommendation is None and state.menu_valid and state.menu_ids:
                    records_by_id = self._recipes(state)
                    records = [records_by_id[key] for key in state.menu_ids if key in records_by_id]
                    if len(records) == len(state.menu_ids):
                        state.last_recommendation = RecommendedMeal(
                            meal_type=state.constraints.meal_type,
                            recipe_ids=[r.recipe_id for r in records], recipe_names=[r.name for r in records],
                        )
                        state.last_recommendation_sequence = state.meal_sequence
                if state.last_recommendation is not None and state.last_recommendation_sequence == state.meal_sequence:
                    state.recent_recommendations = (state.recent_recommendations + [
                        state.last_recommendation.model_copy(deep=True)
                    ])[-HISTORY_LIMIT:]
                state.meal_sequence += 1
                state.menu_ids = []
                state.rejected_recipe_ids = []
                state.pending_method_tradeoff = None
                state.pending_flavor_resolution = None
                state.method_meal_priority_binding = None
                state.constraints.method_meal_priority = None
                if state.meal_constraints is not None:
                    state.meal_constraints.method_meal_priority = None
            if intent.action == "plan" or intent._allergy_guard_plan:
                state.pending_plan = True
            previous_ids = list(state.menu_ids)
            previous_state = state.model_copy(deep=True)
            if intent.action == "reject":
                # Save explicit rejection before clarification/planning can fail.
                record_rejection_action(state, previous_ids)
                state.rejected_recipe_ids = _merge(state.rejected_recipe_ids, previous_ids)
            records_by_id = self._recipes(state)
            current_records = [records_by_id[key] for key in previous_ids if key in records_by_id]
            if intent.action == "replace" and len(current_records) != len(previous_ids):
                target_issue = "原菜单的菜谱来源不完整，请重新核对菜位；不从剩余记录猜测换菜序号。"
            else:
                intent, target_issue = resolve_replacement_target(intent, current_records)
            if intent.action == "replace":
                literal_slots = {request.slot for request in explicit_scoped_methods(message)
                                 if request.slot is not None}
                if literal_slots and literal_slots != {intent.replace_slot}:
                    target_issue = "原文明确的做法换菜位与解析目标不一致，请确认要换第几道；本轮不撤回旧做法要求或更换其他菜位。"
            context_questions: list[ClarificationQuestion] = []
            issue = self._apply_intent(state, intent, message,
                                       context_questions=context_questions, method_menu=current_records,
                                       replacement_confirmed=target_issue is None)
            issue = issue or target_issue
            if intent.action == "replace" and state.menu_ids:
                state.pending_context_replacement = ContextReplacementScope(
                    replace_slot=intent.replace_slot, replace_name=intent.replace_name,
                    target_confirmed=target_issue is None,
                    menu_ids=list(state.menu_ids),
                )
            if issue is None and read_only_authority == "conflict":
                issue = "你同时要求菜单不变和换菜／重新排餐，请确认是只解释，还是允许调整；本轮未修改菜单要求或菜位。"
            pending_flavor = state.pending_flavor_resolution
            if pending_flavor is not None and pending_flavor.binding_hash != flavor_resolution_binding(
                state, catalog_records, pending_flavor.action, pending_flavor.replace_slot,
            ):
                state.pending_flavor_resolution = None
            if state.constraints.method_meal_priority is not None and (
                state.method_meal_priority_binding != priority_binding(state.constraints, catalog_records)
            ):
                state.constraints.method_meal_priority = None
                if state.meal_constraints is not None:
                    state.meal_constraints.method_meal_priority = None
                state.method_meal_priority_binding = None
            pending_tradeoff = state.pending_method_tradeoff
            if pending_tradeoff is not None and pending_tradeoff.binding_hash != pending_binding(
                state.constraints, catalog_records, state.menu_ids,
                pending_tradeoff.action, pending_tradeoff.replace_slot,
            ):
                state.pending_method_tradeoff = None
            if issue is None and intent.action == "clarify":
                # Only the guarded allergy-answer path can accept a clarify intent.
                intent = intent.model_copy(update={"action": "plan", "clarification": None})
            state.revision += 1
            state.last_message = message
            state.menu_valid = False
            state.pending_clarification = issue
            state.history = (state.history + [{"role": "user", "content": message}])[-12:]
            # Valid new restrictions survive planning failures and process restarts.
            self.store.save(state, expected_revision)
            events: list[ToolEvent] = []
            if issue:
                result = self._unresolved(
                    state, issue, "clarification_required", events,
                    context_only=bool(context_questions),
                )
            else:
                result = await self._plan(state, intent, previous_ids, events, previous_state,
                                          flavor_resolution_answer=flavor_resolution_answer,
                                          allow_adjacent_rotation=new_meal_boundary)
            result.timings_ms.update(
                parse=round((parsed - started) * 1000, 2),
                total=round((perf_counter() - started) * 1000, 2),
            )
            state.history = (
                state.history + [{"role": "assistant", "content": result.reason}]
            )[-12:]
            receipt = state.last_recommendation if (
                self.experiment_cross_meal_rotation and result.status == "ok"
            ) else None
            self.store.complete(
                result, state.revision, request_id, request_hash, recommendation=receipt,
                expected_history_revision=state._recommendation_history_revision if receipt else None,
            )
            return result

    def _apply_intent(self, state: SessionState, intent: Intent, message: str,
                      context_questions: list[ClarificationQuestion] | None = None, *,
                      method_menu: list[Recipe] | None = None,
                      replacement_confirmed: bool = False) -> str | None:
        if intent.action == "explain":
            # A model's explanation payload (or quantities quoted in the
            # question) cannot confirm context or rewrite meal/diner facts.
            # A genuine new allergy assertion must still block an old menu:
            # without authorized extraction it remains an unresolved question.
            intent = self._read_only_intent(state, intent, message)
            if intent.diner_updates:
                self._apply_diner_allergy_clarifications(state, intent, message)
            if intent.allergy_clarifications:
                meal = state.meal_constraints or state.constraints.model_copy(deep=True)
                for term, foods in intent.allergy_clarifications.items():
                    state.pending_allergy_terms.remove(term)
                    meal.allergies = _merge(meal.allergies, foods)
                state.meal_constraints = meal
                if not state.pending_allergy_terms:
                    state.pending_allergy = False
            if intent.allergy_clarifications or intent.diner_updates:
                state.constraints = aggregate_constraints(state.meal_constraints or state.constraints, state.diners)
            self._record_unnamed_allergy_questions(state, intent, message)
            if any(re.fullmatch(r"(?:我|本人|本餐|这餐)?(?:不吃辣|不要辣|不辣)[!！]?", part.strip())
                   for part in re.split(r"[，,。；;\n]+|另外|此外", message)):
                state.constraints.no_spicy = True
                if state.meal_constraints is not None:
                    state.meal_constraints.no_spicy = True
            # Reuse all existing safety/context validation on a private copy,
            # with no incoming fields or literal amendment text. Do not clear
            # pending questions or grant planning scope in the persisted state.
            return self._apply_intent(state.model_copy(deep=True), Intent(action="plan"), "")
        intent, scene_issue = apply_scene_withdrawals(state, intent, message)
        intent, flavor_issue = apply_flavor_withdrawals(state, intent, message)
        dietary = ground_diet_intent(message, intent, state.diners)
        intent = dietary.intent
        if dietary.pending_meal:
            state.pending_diet_mode = True
        elif dietary.meal_mode is not None:
            state.pending_diet_mode = False
        counts, structure_issue = explicit_menu_structure(message)
        dessert_count, dessert_issue = explicit_dessert_count(
            message, total_explicit="dish_count" in counts,
        )
        if dessert_issue:
            state.pending_dessert_allocation = True
            structure_issue = dessert_issue
        elif dessert_count is not None:
            state.pending_dessert_allocation = False
        soup_composition, soup_problem = explicit_soup_composition(message)
        if soup_problem:
            state.pending_soup_composition = True
            structure_issue = soup_problem
        elif soup_composition:
            state.pending_soup_composition = False
        composition, composition_problem = explicit_dish_composition(message)
        ungrounded = any(getattr(intent, field) is not None and field not in composition
                         for field in ("meat_dish_count", "vegetarian_dish_count"))
        if ungrounded and not composition_problem:
            composition_problem = "请明确本轮荤菜、素菜各几道，不能用未确认的数量调整菜单。"
        if composition_problem:
            state.pending_dish_composition = True
            intent = intent.model_copy(update={"meat_dish_count": None, "vegetarian_dish_count": None})
        elif composition:
            state.pending_dish_composition = False
        if composition_problem:
            structure_issue = composition_problem
        if counts:
            intent = intent.model_copy(update=counts)
        if composition:
            intent = intent.model_copy(update=composition)
        active_diners = [diner for diner in state.diners if diner.attendance]
        pending_terms = list(state.pending_allergy_terms)
        for diner in active_diners:
            for term in diner.pending_allergy_terms:
                pending_terms.extend([term, f"{diner.display_name}的{term}"])
        was_pending_allergy = state.pending_allergy or bool(pending_terms) or any(
            diner.pending_allergy for diner in active_diners
        )
        global_pending = bool(state.pending_allergy or state.pending_allergy_terms)
        resolution_context_count = int(global_pending) + sum(
            bool(diner.pending_allergy or diner.pending_allergy_terms)
            for diner in active_diners
        )
        pending_owner_names = list(dict.fromkeys([
            *(["我", "本人", "用户"] if global_pending else []),
            *(name for diner in active_diners
              if diner.pending_allergy or diner.pending_allergy_terms
              for name in [diner.display_name, *diner.aliases]),
        ]))
        pending_meal_type = state.constraints.meal_type
        meal_constraints = state.meal_constraints or state.constraints.model_copy(deep=True)
        state.meal_constraints = meal_constraints
        constraints = meal_constraints
        count_issue = apply_menu_counts(state, intent)
        if dessert_count is not None:
            constraints.dessert_count = dessert_count
            state.menu_structure_explicit = True
        restore_issue = apply_constraint_restore(state, intent)
        revoke_issue = apply_revoke_exclusion(state, intent, message)
        if intent.action in {"plan", "replace", "reject"}:
            constraints.scoped_methods = amend_scoped_methods(
                constraints.scoped_methods, explicit_scoped_methods(message),
                menu=method_menu or (), local=intent.action == "replace",
                replace_slot=intent.replace_slot if replacement_confirmed else None,
                message=message,
            )
        for name, value in soup_composition.items():
            setattr(constraints, name, value)
        if counts.get("soup_count") == 0 and not structure_issue and not soup_composition:
            # Explicit cancellation removes only old soup quantities, never
            # animal-source, allergy, non-spicy or whole-meal diet facts.
            constraints.meat_soup_count = constraints.vegetarian_soup_count = None
            state.pending_soup_composition = False
        if not composition_problem and independent_meat_requested(message):
            constraints.meat_dish_scope = "independent_entree"
        confirm_from_intent(state, intent, message)
        repair_proven_reference_pending(state)
        # Unknown terms are pending questions, not permanent hard constraints.
        # Clarification can resolve them, while already known allergens never disappear.
        old_unknown = self.rules.unresolved_allergies(constraints)
        if old_unknown:
            constraints.allergies = [term for term in constraints.allergies if term not in old_unknown]
            state.pending_allergy_terms = _merge(state.pending_allergy_terms, old_unknown)
            state.pending_allergy = True
        resolved_values = []
        for pending, replacements in intent.allergy_clarifications.items():
            if (
                pending in state.pending_allergy_terms and replacements
                and not self.rules.unresolved_allergies(Constraints(allergies=replacements))
                and explicit_allergy_resolution(
                    message, state, replacements, pending_term=pending,
                    owner_names=("我", "本人", "用户"),
                    require_owner=resolution_context_count > 1,
                )
            ):
                state.pending_allergy_terms.remove(pending)
                resolved_values.extend(replacements)
        incoming = _merge(intent.allergies, resolved_values)
        incoming_unknown = self.rules.unresolved_allergies(Constraints(allergies=incoming))
        known_incoming = [term for term in incoming if term not in incoming_unknown]
        if known_incoming and not incoming_unknown and not state.pending_allergy_terms:
            # An unnamed question may be answered with a named allergen, but
            # an additive phrase leaves the previous uncertainty unresolved.
            if (
                not re.search(r"另外|此外|还有|也过敏|还过敏|新增|再加", message)
                and (resolved_values or explicit_allergy_resolution(
                    message, state, known_incoming, owner_names=("我", "本人", "用户"),
                    require_owner=resolution_context_count > 1,
                ))
            ):
                state.pending_allergy = False
        state.pending_allergy_terms = _merge(state.pending_allergy_terms, incoming_unknown)
        if state.pending_allergy_terms:
            state.pending_allergy = True
        constraints.allergies = _merge(constraints.allergies, known_incoming)
        constraints.excluded_ingredients = _merge(
            constraints.excluded_ingredients, intent.excluded_ingredients
        )
        constraints.health_goals = _merge(constraints.health_goals, intent.health_goals)
        constraints.preferences = _merge(constraints.preferences, intent.preferences)
        constraints.preferred_ingredients = _merge(
            constraints.preferred_ingredients, intent.preferred_ingredients
        )
        previous_active_count = effective_attendee_count(state.diners)
        previous_attendance = {
            diner.diner_id: (diner.attendance, diner.participation_basis)
            for diner in state.diners
        }
        try:
            state.diners = apply_diner_updates(
                state.diners, intent.diner_updates, session_id=state.session_id, message=message
            )
            # Global extraction already enforces this food across the meal;
            # a literal self assertion also belongs to the stable owner. Do
            # not migrate/delete shared constraints or invent participation.
            self_allergies = explicit_self_allergy_facts(message, known_incoming)
            for diner in state.diners:
                if diner.profile_owner:
                    diner.allergies = _merge(diner.allergies, self_allergies)
            for owner in dietary.pending_owners:
                diner = find_diner(state.diners, DinerUpdate(diner=owner))
                if diner is not None:
                    diner.pending_diet_mode = True
            self._apply_diner_allergy_clarifications(
                state, intent, message, resolution_context_count=resolution_context_count,
            )
            self._record_unnamed_allergy_questions(state, intent, message)
        except DinerConflict as error:
            state.constraints = aggregate_constraints(constraints, state.diners)
            return str(error)
        for name in ("dish_count", "soup_count", "people", "max_minutes", "meat_dish_count", "vegetarian_dish_count", "diet_mode"):
            if name in {"dish_count", "soup_count"}:
                # The pending-count adapter applies the pair atomically and
                # preserves the last effective counts on a contradiction.
                continue
            value = getattr(intent, name)
            if value is not None:
                setattr(constraints, name, value)
        attendance_changed = any(
            update.attendance is not None for update in intent.diner_updates
        ) or any(
            diner.diner_id in previous_attendance
            and previous_attendance[diner.diner_id] != (diner.attendance, diner.participation_basis)
            for diner in state.diners
        )
        active_count = effective_attendee_count(state.diners)
        if (
            attendance_changed
            and intent.people is None
            and "people" in state.confirmed_fields
            and previous_active_count == constraints.people
        ):
            constraints.people = max(1, active_count)
        if active_count > constraints.people and "people" in state.confirmed_fields:
            state.constraints = aggregate_constraints(constraints, state.diners)
            return (
                f"当前记录了 {active_count} 位参餐者，但总人数是 {constraints.people} 人；"
                "请确认谁参加本餐。"
            )
        if (intent.people is not None or attendance_changed) and not state.menu_structure_explicit:
            self._apply_party_structure_defaults(constraints)
        state.constraints = aggregate_constraints(constraints, state.diners)
        if intent.meal_type:
            if intent.meal_type not in {"早餐", "午餐", "晚餐", "夜宵", "下午茶"}:
                return "请说明要安排早餐、午餐、晚餐还是加餐。"
            constraints.meal_type = intent.meal_type
        if intent.inventory is not None:
            constraints.inventory = intent.inventory
        if intent.no_spicy is True:
            constraints.no_spicy = True
        elif intent.no_spicy is False and state.constraints.no_spicy:
            return "当前会话保留了不吃辣的要求；如要撤销，请另开会话明确本餐要求。"
        if intent.clear_time_limit:
            if re.search(
                r"时间(?:不限制|不限|不作限制)|不限时间|不限制.*时间|取消.*时间|不限定时间|(?:去掉|不设|不要|不用|没有).*时间限制|不赶时间",
                message,
            ):
                constraints.max_minutes = None
            else:
                return "请明确是否取消总耗时限制。"
        state.constraints = aggregate_constraints(constraints, state.diners)
        if state.pending_allergy:
            details = "（请逐一明确：" + "、".join(state.pending_allergy_terms) + "）" if state.pending_allergy_terms else ""
            return "尚未明确具体过敏食材，请先补充过敏信息" + details + "；此前菜单暂不作为可用建议。"
        pending_diners = [
            diner for diner in state.diners
            if diner.attendance and (diner.pending_allergy or diner.pending_allergy_terms)
        ]
        if pending_diners:
            return "；".join(
                f"{diner.display_name}的过敏原缺少可靠映射，请明确具体食材："
                + ("、".join(diner.pending_allergy_terms) or "尚未说明")
                for diner in pending_diners
            ) + "；此前菜单暂不作为可用建议。"
        unresolved = self.rules.unresolved_allergies(state.constraints)
        if unresolved:
            return "当前词典无法确认这些过敏原，请明确具体食材：" + "、".join(unresolved)
        if revoke_issue:
            return revoke_issue
        if count_issue:
            return count_issue
        if restore_issue:
            return restore_issue
        if scene_issue:
            # Soft amendment ambiguity must not discard new safety facts.
            return scene_issue
        if flavor_issue:
            if flavor_conflicts(state.constraints.preferences):
                # A migrated conflicting snapshot may not yet have a bound
                # question. Generate real source options, never a dead-end
                # instruction to answer a question that does not exist.
                state.pending_flavor_resolution = make_flavor_resolution(
                    state, list(self._recipes(state).values()),
                    "replace" if intent.action == "replace" else "reject" if intent.action == "reject" else "plan",
                    intent.replace_slot,
                )
                return state.pending_flavor_resolution.prompt
            return flavor_issue
        if issue := context_conflict_issue(state.constraints):
            return issue
        if structure_issue:
            return structure_issue
        if state.pending_dessert_allocation:
            return "饭后甜点的数量口径仍待确认，请明确总共几道（含汤和甜点）、其中几道甜点，或明确取消甜点。"
        if (state.constraints.dessert_count
                and state.constraints.dessert_count + state.constraints.soup_count >= state.constraints.dish_count):
            return "总菜数包含汤和甜点，至少保留一道正餐；请确认菜数、汤数及甜点数。"
        if state.pending_diet_mode:
            return DIET_QUESTION
        pending_diets = [diner.display_name for diner in state.diners if diner.attendance and diner.pending_diet_mode]
        if pending_diets:
            return "、".join(pending_diets) + "的饮食模式尚未明确。" + DIET_QUESTION
        if issue := diet_issue(state.constraints):
            return issue
        if state.pending_dish_composition:
            return "荤素数量尚未明确，请写明本餐荤菜、素菜各几道；不使用上轮含糊表达猜测数量。"
        if state.pending_soup_composition:
            return "汤的来源数量尚未明确，请确认荤汤、素汤各几道及总汤数；不猜测未确认的限定。"
        if issue := composition_issue(state.constraints):
            return issue
        if state.constraints.soup_count > state.constraints.dish_count:
            return "汤的数量不能超过总菜数，请明确总共几道，其中几道汤。"
        resolved_foods = incoming + [
            food for update in intent.diner_updates
            for food in update.allergies + [
                value for values in update.allergy_clarifications.values() for value in values
            ]
        ]
        resume_plan = (
            intent.action == "clarify" and state.pending_plan and was_pending_allergy
            and not missing_questions(state)
            and state.constraints.meal_type == pending_meal_type
            and allergy_answer_only(
                message, pending_terms, resolved_foods,
                owner_names=pending_owner_names, meal_type=pending_meal_type,
                no_spicy=state.constraints.no_spicy,
            )
        )
        if (intent.action == "clarify" or intent.clarification) and not resume_plan:
            return intent.clarification or "请补充本餐需要调整的具体要求。"
        questions = missing_questions(state)
        if questions:
            if context_questions is not None:
                context_questions.extend(questions)
            return clarification_copy(questions)
        if intent.method_meal_priority is not None:
            constraints.method_meal_priority = intent.method_meal_priority
            state.constraints = aggregate_constraints(constraints, state.diners)
        menu_restore_issue = apply_menu_restore(
            state, intent, self.rules, self._recipes(state)
        )
        if menu_restore_issue:
            return menu_restore_issue
        return None

    def _read_only_intent(self, state: SessionState, intent: Intent, message: str) -> Intent:
        """Only literal answers to existing safety questions survive no-edit scope.

        Menu context/preferences/attendance remain frozen. Named pending safety
        mappings can be acknowledged without granting any plan/resume authority.
        """
        contexts = int(state.pending_allergy or bool(state.pending_allergy_terms)) + sum(
            bool(d.pending_allergy or d.pending_allergy_terms) for d in state.diners if d.attendance
        )
        def grounded(mapping: dict[str, list[str]], terms: list[str], owners: list[str]) -> dict[str, list[str]]:
            return {term: foods for term, foods in mapping.items() if term in terms and foods
                    and not self.rules.unresolved_allergies(Constraints(allergies=foods))
                    and explicit_allergy_resolution(message, state, foods, pending_term=term,
                        owner_names=owners, require_owner=contexts > 1)}
        global_answers = grounded(intent.allergy_clarifications, state.pending_allergy_terms, ["我", "本人", "用户"])
        updates = []
        for update in intent.diner_updates:
            try:
                owner = find_diner(state.diners, DinerUpdate(diner=update.diner))
            except DinerConflict:
                continue
            if owner is None:
                continue
            answers = grounded(update.allergy_clarifications, owner.pending_allergy_terms,
                               [owner.display_name, *owner.aliases])
            if answers:
                updates.append(DinerUpdate(diner=owner.display_name, allergy_clarifications=answers))
        return Intent(action="explain", allergy_clarifications=global_answers, diner_updates=updates)

    def _apply_diner_allergy_clarifications(
        self, state: SessionState, intent: Intent, message: str, *, resolution_context_count: int = 0,
    ) -> None:
        """Persist known facts first; resolve uncertainty only for its named owner."""
        for diner in state.diners:
            unknown = self.rules.unresolved_allergies(Constraints(allergies=diner.allergies))
            diner.allergies = [term for term in diner.allergies if term not in unknown]
            diner.pending_allergy_terms = _merge(diner.pending_allergy_terms, unknown)
        contexts = max(
            resolution_context_count,
            int(state.pending_allergy or bool(state.pending_allergy_terms)) + sum(
                bool(d.pending_allergy or d.pending_allergy_terms)
                for d in state.diners if d.attendance
            ),
        )
        for update in intent.diner_updates:
            diner = find_diner(state.diners, update)
            if diner is None:
                continue
            scoped_state = state.model_copy(update={
                "pending_allergy_terms": list(diner.pending_allergy_terms),
            })
            if (
                diner.pending_allergy
                and update.allergies
                and explicit_allergy_resolution(
                    message, scoped_state, update.allergies,
                    owner_names=[diner.display_name, *diner.aliases],
                    require_owner=contexts > 1,
                )
            ):
                # A named but unmapped answer transfers the block to pending terms;
                # it must not leave a second, unanswerable unnamed question behind.
                diner.pending_allergy = False
            for pending, replacements in update.allergy_clarifications.items():
                if (
                    pending in diner.pending_allergy_terms
                    and replacements
                    and not self.rules.unresolved_allergies(Constraints(allergies=replacements))
                    and explicit_allergy_resolution(
                        message, scoped_state, replacements, pending_term=pending,
                        owner_names=[diner.display_name, *diner.aliases],
                        require_owner=contexts > 1,
                    )
                ):
                    diner.pending_allergy_terms.remove(pending)
                    diner.allergies = _merge(diner.allergies, replacements)

    def _record_unnamed_allergy_questions(
        self, state: SessionState, intent: Intent, message: str
    ) -> None:
        """Attribute only complete, explicit short assertions; never guess a person."""
        for mention in uncovered_allergy_mentions(message, intent, state):
            if mention.owner is None or mention.owner == "用户":
                state.pending_allergy = True
                continue
            update = DinerUpdate(diner=mention.owner, attendance=mention.active)
            diner = find_diner(state.diners, update)
            if diner is None:
                state.diners = apply_diner_updates(state.diners, [update], session_id=state.session_id)
                diner = find_diner(state.diners, update)
            if diner is not None:
                diner.pending_allergy = True

    @staticmethod
    def _apply_party_structure_defaults(constraints: Constraints) -> None:
        """Apply documented engineering defaults only when structure was not explicit."""
        if constraints.people <= 2:
            constraints.dish_count = 3
            constraints.soup_count = 0
        elif constraints.people <= 4:
            constraints.dish_count = 4
            constraints.soup_count = 1
        elif constraints.people <= 6:
            constraints.dish_count = 5
            constraints.soup_count = 1
        else:
            constraints.dish_count = 6
            constraints.soup_count = 1

    @staticmethod
    def _ensure_diner_state(state: SessionState, profile: UserProfile) -> None:
        """Upgrade pre-PR3 session snapshots conservatively on first access."""
        if state.meal_constraints is None:
            state.meal_constraints = state.constraints.model_copy(deep=True)
        if not state.diners:
            state.diners = [profile_diner(profile)]
        elif explicit_non_spicy_flavor_preference(profile.preferences):
            # Older snapshots did not seed this profile safety flag. Upgrade
            # only the owner; attendance aggregation still determines sharing.
            for diner in state.diners:
                if diner.profile_owner:
                    diner.no_spicy = True
        state.constraints = aggregate_constraints(state.meal_constraints, state.diners)

    def _constraints_text(
        self,
        constraints: Constraints,
        confirmed: list[str],
        diners: list[Diner] | None = None,
    ) -> list[str]:
        people = f"{constraints.people} 人" if "people" in confirmed else "人数待确认"
        meal = constraints.meal_type if "meal_type" in confirmed else "餐次待确认"
        items = [f"{people}，{meal}，共 {constraints.dish_count} 道（含汤）"]
        if constraints.dessert_count:
            items[0] = f"{people}，{meal}，共 {constraints.dish_count} 道（含汤和 {constraints.dessert_count} 道饭后甜点）"
        if constraints.allergies:
            items.append("已列过敏食材：" + "、".join(constraints.allergies))
        if constraints.excluded_ingredients:
            items.append("排除食材：" + "、".join(constraints.excluded_ingredients))
        if constraints.no_spicy:
            items.append("共享菜单不含已识别的辣味配料")
        if constraints.health_goals:
            items.append("定性排序目标：" + "、".join(constraints.health_goals))
        if constraints.max_minutes:
            items.append(f"要求整餐不超过 {constraints.max_minutes} 分钟（当前数据无法验证）")
        if constraints.inventory is not None:
            items.append("仅使用指定食材：" + "、".join(constraints.inventory))
        for diner in diners or []:
            if not diner.attendance:
                continue
            details = []
            if diner.allergies:
                details.append("过敏=" + "、".join(diner.allergies))
            if diner.excluded_ingredients:
                details.append("不吃=" + "、".join(diner.excluded_ingredients))
            if diner.no_spicy:
                details.append("不吃辣")
            if is_unlinked_profile(diner):
                items.append(
                    "档案主体（身份待关联，共享限制）："
                    + ("；".join(details) if details else "个人资料保留，未关联到指定参餐者")
                )
            elif details:
                items.append(f"{diner.display_name}：" + "；".join(details))
        active_count = effective_attendee_count(diners or [])
        if "people" in confirmed and active_count < constraints.people:
            items.append(f"其余 {constraints.people - active_count} 位用餐者的个人限制尚未提供")
        return items

    def _unresolved(
        self, state: SessionState, reason: str, status: str, events: list[ToolEvent],
        *, context_only: bool = False,
    ) -> ChatResult:
        if state.last_flavor_retraction is not None:
            reason = retraction_copy(state.last_flavor_retraction) + "\n" + reason
        if state.last_scene_retractions:
            reason = scene_retraction_copy(state) + "\n" + reason
        if state.last_direct_flavor_retractions:
            reason = direct_flavor_retraction_copy(state) + "\n" + reason
        state.menu_valid = False
        state.pending_clarification = reason if status == "clarification_required" else None
        questions = []
        if status == "clarification_required":
            questions = missing_questions(state)
            if (
                state.pending_allergy
                or any(
                    d.attendance and (d.pending_allergy or d.pending_allergy_terms)
                    for d in state.diners
                )
                or self.rules.unresolved_allergies(state.constraints)
            ):
                questions = [question for question in questions if question.field != "restrictions"]
                questions.insert(0, ClarificationQuestion(field="allergy", prompt=reason))
            elif not questions and state.pending_flavor_resolution is not None:
                questions = [ClarificationQuestion(field="flavor_resolution",
                    prompt=state.pending_flavor_resolution.prompt,
                    options=[option.display for option in state.pending_flavor_resolution.options] + ["暂不规划"])]
            elif not questions and state.pending_method_tradeoff is not None:
                questions = [ClarificationQuestion(
                    field="method_meal_priority", prompt=state.pending_method_tradeoff.prompt,
                    options=list(METHOD_MEAL_OPTIONS),
                )]
            elif state.pending_menu_counts is not None:
                questions.insert(0, ClarificationQuestion(field="request", prompt=reason))
            elif not questions and state.constraints.max_minutes is not None:
                questions = [ClarificationQuestion(
                    field="time_limit", prompt=reason, options=["取消时间限制", "暂不规划"],
                )]
            elif not questions:
                questions = [ClarificationQuestion(field="request", prompt=reason)]
            elif not context_only:
                questions.insert(0, ClarificationQuestion(field="request", prompt=reason))
        state.pending_fields = [question.field for question in questions]
        return ChatResult(
            status=status, reason=reason,
            constraints=self._constraints_text(
                state.constraints, state.confirmed_fields, state.diners
            ),
            conversation_state=state, tool_calls=events, clarification_questions=questions,
            warnings=["旧菜单尚未通过当前约束校验，不作为本轮推荐。"],
        )

    def _item(
        self, recipe: Recipe, slot: int, constraints: Constraints, events: list[ToolEvent]
    ) -> MenuItem:
        decision = self.health_tool.evaluate(recipe, constraints)
        verified_generated = verified_generated_recipe(recipe)
        if (
            not decision.allowed
            or not is_menu_recipe(recipe, constraints)
            or (recipe.recipe_id not in self.catalog.recipes and not verified_generated)
        ):
            raise RuntimeError("Final recipe validation failed")
        prep_notice = advance_preparation_copy([recipe])
        return MenuItem(
            slot=slot, recipe_id=recipe.recipe_id, name=recipe.name,
            source=("生成器新提案（待试做，非原菜谱库）" if verified_proposal(recipe)
                    else "本地新生成方案（待试做，非原菜谱库）" if verified_generated
                    else "方太菜谱库"),
            ingredients=[ingredient.name for ingredient in recipe.ingredients],
            steps=recipe.steps, reasons=[*decision.reasons, *(
                warning for warning in decision.warnings
                if warning.startswith(recipe.name + "：")
            ), *([prep_notice] if prep_notice else [])],
            card=build_card(recipe),
            ingredient_details=[ingredient.model_copy(deep=True) for ingredient in recipe.ingredients],
            cooking_steps=split_cooking_steps(recipe.steps), provenance=recipe_provenance(recipe),
            nutrition=self.tools.call(
                "nutrition_analysis", events, recipe=recipe, constraints=constraints, detailed=True,
            ),
            nutrition_notes=self.tools.call(
                "nutrition_analysis", events, recipe=recipe, constraints=constraints
            ),
        )

    async def _plan(
        self, state: SessionState, intent: Intent, previous_ids: list[str],
        events: list[ToolEvent], previous_state: SessionState | None = None,
        *, flavor_resolution_answer: bool = False,
        allow_adjacent_rotation: bool = False,
    ) -> ChatResult:
        # A safety/context answer can resume planning after initial scope
        # handling. Rebind its saved edit obligation here too, not just parse.
        intent = preserve_context_retry_scope(state, intent, "")
        started = perf_counter()
        constraints = state.constraints
        if intent.action == "clarify":
            return self._unresolved(state, intent.clarification or "请先明确本餐要求。",
                                    "clarification_required", events)
        if state.pending_flavor_resolution is not None:
            return self._unresolved(state, state.pending_flavor_resolution.prompt,
                                    "clarification_required", events)
        if state.pending_method_tradeoff is not None:
            return self._unresolved(
                state, state.pending_method_tradeoff.prompt, "clarification_required", events,
            )
        rejected_ids = set(state.rejected_recipe_ids)
        rejected_ids.update(intent._replacement_exclusions)
        records_by_id = self._recipes(state)
        rejected_names = {
            compact(records_by_id[key].name)
            for key in rejected_ids if key in records_by_id
        }
        # Multiple catalog rows may represent the same named dish. Do not
        # reintroduce a rejected dish through another recipe ID or a suggestion.
        rejected_ids.update(
            recipe.recipe_id for recipe in records_by_id.values()
            if compact(recipe.name) in rejected_names
        )
        if constraints.max_minutes is not None:
            return self._unresolved(
                state,
                "菜谱缺少可验证的整餐总耗时，无法保证该时间上限。是否取消时间限制，先按其他要求规划？",
                "clarification_required", events,
            )
        current = [records_by_id[key] for key in previous_ids if key in records_by_id]
        if intent.action == "replace":
            intent, target_issue = resolve_replacement_target(intent, current)
            if target_issue:
                return self._unresolved(state, target_issue, "clarification_required", events)
        replace_slot = intent.replace_slot
        if issue := context_conflict_issue(constraints):
            return self._unresolved(state, issue, "clarification_required", events)
        if flavor_conflicts(constraints.preferences):
            state.pending_flavor_resolution = make_flavor_resolution(
                state, list(records_by_id.values()), intent.action, replace_slot,
            )
            return self._unresolved(state, state.pending_flavor_resolution.prompt,
                                    "clarification_required", events)
        restored = intent.restore_menu is not None
        if restored:
            # apply_menu_restore already revalidated these IDs and put them on
            # state.menu_ids. Restore the exact menu, never a re-planned one.
            chosen = [
                records_by_id[rid] for rid in state.menu_ids if rid in records_by_id
            ]
            if len(chosen) != len(state.menu_ids) or len({recipe.recipe_id for recipe in chosen}) != len(chosen):
                return self._unresolved(
                    state, "历史菜单中的菜品当前已不可用，无法恢复原菜单。",
                    "clarification_required", events,
                )
            planning = PlanResult(recipes=chosen, changes=[], warnings=[], failure=None)
            safe = []
        elif intent.action == "explain" or intent._retain_completed_menu:
            verified_current = self.tools.call(
                "health_check", events, recipes=current, constraints=constraints,
            )
            if (
                len(current) != constraints.dish_count
                or sum("soup" in recipe.categories for recipe in current) != constraints.soup_count
                or len(verified_current) != len(current)
                or any(recipe.recipe_id in rejected_ids for recipe in current)
                or any(not is_menu_recipe(recipe, constraints) for recipe in current)
                or not dessert_structure_satisfied(current, constraints)
                or not composition_satisfied(current, constraints)
                or missing_scoped_methods(current, constraints.scoped_methods, required_only=True)
            ):
                return self._unresolved(
                    state, "当前没有满足已知约束的完整菜单，请先规划本餐。",
                    "clarification_required", events,
                )
            _, _, missing_preference_warnings = self.planner._cover_ingredient_preferences(
                current, {}, {}, constraints, current, None, {}, allow_repair=False,
            )
            planning = PlanResult(
                recipes=current, changes=[], warnings=missing_preference_warnings, failure=None,
            )
            safe = []
        else:
            if self.experiment_cross_meal_rotation and not current:
                history = self.store.recommendation_history(
                    state.user_id, exclude=(state.session_id, state.meal_sequence),
                )
                state._recommendation_history_revision = history.revision
                # The database is authoritative for cross-session history.
                # Empty history may retain only the legacy session-local archive.
                if history.recommendations:
                    state.recent_recommendations = history.recommendations
            query_terms = list(dict.fromkeys(intent.query_terms + constraints.preferred_ingredients))
            candidates = self.tools.call(
                "recipe_search", events, query_terms=query_terms, constraints=constraints, limit=None
            )
            safe = self.tools.call(
                "health_check", events, recipes=candidates, constraints=constraints
            )
            safe = [recipe for recipe in safe if recipe.recipe_id not in rejected_ids]
            method_replan = intent.action == "plan" and any(
                request.slot is None and request.required
                and request in constraints.scoped_methods
                for request in explicit_scoped_methods(state.last_message)
            )
            recheck = method_replan or flavor_resolution_answer or not current or intent.action in {"reject", "replace"} or any((
                intent.health_goals, intent.preferences, intent.preferred_ingredients,
                intent.allergies, intent.excluded_ingredients, intent.diner_updates,
                intent.no_spicy is not None, intent.inventory is not None,
                intent.meal_type is not None, intent.people is not None,
                intent.dish_count is not None, intent.soup_count is not None,
                intent.diet_mode is not None, intent.method_meal_priority is not None,
                intent.meat_dish_count is not None, intent.vegetarian_dish_count is not None,
            ))
            planning = self.tools.call(
                "menu_modify", events, candidates=safe, constraints=constraints, current=current,
                replace_slot=replace_slot,
                reject_ids=rejected_ids,
                query_terms=query_terms,
                # An empty continuation is not permission to optimize slots
                # outside a previously protected local edit. New constraints,
                # meal structure or explicit reject/replace do trigger review.
                recheck_soft_preferences=recheck,
                recent_recipe_names=[m.recipe_names for m in state.recent_recommendations]
                if self.experiment_cross_meal_rotation and not current
                else None,
                allow_adjacent_rotation=allow_adjacent_rotation,
            )
            # Generate only after full source retrieval and its normal plan.
            # Explain and empty continuation grant no synthesis authority.
            # An explicit added food request may repair a retained menu; a
            # local edit must stay within its target slot.
            generation_scope = (
                (intent.action == "plan" and (
                    not current or method_replan or (
                        bool(intent.preferred_ingredients)
                        and not empty_continuation_request(state.last_message)
                    )
                ))
                or (intent.action == "replace" and replace_slot is not None)
            )
            generation_base = current if replace_slot is not None else planning.recipes
            # A grounded whole-meal method update can invalidate a retained
            # menu too. Retry its normal full-source plan with the same bounded
            # proposal; local edits and readonly/continue gain no extra scope.
            source_retry = bool(planning.failure and (not current or method_replan))
            if generation_scope and (not planning.failure or replace_slot is not None or source_retry):
                positions: list[int | None] = [None] if source_retry else (
                    [replace_slot - 1] if replace_slot is not None
                    else [i for i, recipe in enumerate(generation_base)
                          if recipe.categories == ["protein"]]
                )
                positions = [i for i in positions if i is None or generation_base[i].categories == ["protein"]]
                # A local request to replace a generated tofu may propose a
                # different method, but other tofu slots still take precedence.
                gap_menu = ([r for i, r in enumerate(generation_base) if i != replace_slot - 1]
                            if replace_slot is not None else generation_base)
                proposal = propose_missing_tofu(
                    constraints, candidates, gap_menu, rejected_ids, self.rules,
                ) if positions else None
                tofu_proposal = proposal is not None
                if proposal is None:
                    proposal = propose_missing_liangfen(
                        constraints, list(self.catalog.recipes.values()), gap_menu,
                        rejected_ids, self.rules,
                    )
                    if proposal is not None:
                        positions = [None] if source_retry else (
                            [replace_slot - 1] if replace_slot is not None
                            else list(range(len(generation_base)))
                        )
                        positions = [i for i in positions if i is None
                                     or generation_base[i].categories == proposal.categories]
                generation_warnings: list[str] = []
                if tofu_proposal and self.allow_recipe_generation:
                    try:
                        draft = await self.llm.propose_recipe([
                            item["content"] for item in state.history
                            if item.get("role") == "user" and isinstance(item.get("content"), str)
                        ][-6:])
                        if draft is not None:
                            new_proposal = normalize_proposal(draft)
                            if (not self.rules.evaluate(new_proposal, constraints).allowed
                                    or new_proposal.recipe_id in rejected_ids
                                    or compact(new_proposal.name) in rejected_names
                                    or any(not scoped_method_mask(new_proposal, [request], slot=request.slot)
                                           for request in constraints.scoped_methods
                                           if self.rules.canonical_food(request.food) == "豆腐" and request.required)):
                                raise ValueError("Proposal violates local constraints")
                            proposal = new_proposal
                        else:
                            generation_warnings.append("生成器未提出可核验新菜，保留本地基础提案；不表示其他做法不存在。")
                    except (LLMUnavailable, ValueError):
                        generation_warnings.append("新菜生成或本地校验未通过，未采用其配方；仅尝试已有基础提案，不放宽已确认限制。")
                if proposal is not None:
                    for index in positions:
                        generated_plan = self.tools.call(
                            "menu_modify", events,
                            candidates=[*safe, proposal] if index is None else [proposal],
                            constraints=constraints,
                            current=generation_base, replace_slot=None if index is None else index + 1,
                            reject_ids=rejected_ids, query_terms=query_terms,
                            recent_recipe_names=[m.recipe_names for m in state.recent_recommendations]
                            if self.experiment_cross_meal_rotation and not current else None,
                            allow_adjacent_rotation=allow_adjacent_rotation,
                        )
                        if generated_plan.failure or proposal not in generated_plan.recipes:
                            continue
                        unchanged = index is None or all(
                            old == new for i, (old, new) in enumerate(
                                zip(generation_base, generated_plan.recipes)
                            ) if i != index
                        )
                        preserved = all(
                            not any(self.rules.preference_matches(recipe, food) for recipe in generation_base)
                            or any(self.rules.preference_matches(recipe, food) for recipe in generated_plan.recipes)
                            for food in constraints.preferred_ingredients
                        )
                        if not unchanged or not preserved:
                            continue
                        events.append(ToolEvent(name="recipe_generate", summary={
                            "count": 1,
                            "generator_version": generator_version(proposal),
                            "proposal_provider_enabled": tofu_proposal and self.allow_recipe_generation,
                            "proposal_only": True,
                        }))
                        # Write only a successfully selected, normalized record.
                        # SessionStore persists this server-only field locally.
                        if verified_proposal(proposal):
                            state.generated_recipes[proposal.recipe_id] = proposal
                        planning = generated_plan
                        planning.warnings.extend(generation_warnings)
                        safe = [*safe, proposal]
                        records_by_id[proposal.recipe_id] = proposal
                        break
                if planning.failure and generation_warnings:
                    planning.failure += " " + " ".join(generation_warnings)
            # The existing method/meal alternatives contain ordinary dishes
            # only; they must not discard explicit dessert allocations.
            if not planning.failure and recheck and not constraints.dessert_count:
                trade_candidates = safe
                if replace_slot is not None:
                    target = current[replace_slot - 1]
                    trade_candidates = [r for r in safe if r.recipe_id != target.recipe_id
                                        and compact(r.name) != compact(target.name)]
                tradeoff = find_tradeoff(planning.recipes, trade_candidates, constraints,
                                         self.rules, replace_slot=replace_slot)
                if tradeoff is not None:
                    state.pending_method_tradeoff = make_pending(
                        *tradeoff, constraints, list(records_by_id.values()), previous_ids,
                        "replace" if replace_slot is not None else "reject" if intent.action == "reject" else "plan",
                        replace_slot,
                    )
                    return self._unresolved(state, state.pending_method_tradeoff.prompt,
                                            "clarification_required", events)
        if planning.failure:
            return self._unresolved(state, planning.failure, "no_feasible_menu", events)
        chosen = planning.recipes
        if (
            len(chosen) != constraints.dish_count
            or len({recipe.recipe_id for recipe in chosen}) != len(chosen)
            or sum("soup" in recipe.categories for recipe in chosen) != constraints.soup_count
            or not dessert_structure_satisfied(chosen, constraints)
            or any(recipe.recipe_id in rejected_ids for recipe in chosen)
            or not composition_satisfied(chosen, constraints)
        ):
            raise RuntimeError("Final menu structure validation failed")
        menu = [self._item(recipe, i + 1, constraints, events) for i, recipe in enumerate(chosen)]
        chosen_ids = [recipe.recipe_id for recipe in chosen]
        suggestions: list[MenuItem] = []
        # Keep the current slot's role and verified food constraints while
        # rotating equally suitable alternatives between conversations.
        for candidate in replacement_candidates(chosen, safe, state.session_id, constraints=constraints, rules=self.rules):
            suggestion = self._item(candidate, 1, constraints, events)
            suggestion.replacement_reason = "可替换第 1 道菜，其他菜不变；食材限制已核对。"
            suggestions.append(suggestion)
        nutrition = self.tools.call(
            "nutrition_analysis", events, recipes=chosen, constraints=constraints,
        )
        menu_balance = analyze_menu_balance(chosen, constraints)
        suitability = diner_suitability(chosen, state.diners, self.rules)
        if any(not item.hard_constraints_satisfied for item in suitability):
            raise RuntimeError("Final per-diner validation failed")
        state.menu_ids = chosen_ids
        if self.experiment_cross_meal_rotation:
            state.last_recommendation = RecommendedMeal(
                meal_type=constraints.meal_type, recipe_ids=chosen_ids,
                recipe_names=[r.name for r in chosen],
            )
            state.last_recommendation_sequence = state.meal_sequence
        state.menu_valid = True
        if intent.action != "explain":
            state.pending_context_replacement = None
        state.pending_plan = False
        state.pending_clarification = None
        state.pending_fields = []
        record_menu_revision(state, chosen_ids, source="restored_menu" if restored else "planned_menu")
        facts = response_facts(
            intent=intent,
            constraints=constraints,
            diners=state.diners,
            previous=current,
            chosen=chosen,
            balance=menu_balance,
            health_matches=nutrition.goal_matches,
        )
        source_tradeoffs = [
            text for text in planning.warnings
            if text.startswith(("护心食物选择参考：", "为优先有完整原方主体依据"))
        ]
        food_gaps = [
            text for text in planning.warnings
            if text.startswith(("当前菜单未覆盖食材偏好：", "尚未覆盖有实际食材依据的蛋白菜名称参考："))
        ]
        if food_gaps:
            facts["food_preferences"] = "\n".join(food_gaps)
            if intent.action == "plan":
                facts["opening"] = (
                    "本餐已按已知硬约束安排；仍有食材偏好未满足，具体缺口见下。"
                    "未擅自放宽素食、过敏、不辣或数量要求。"
                )
        if source_tradeoffs:
            facts["source_food_tradeoff"] = "\n".join(source_tradeoffs)
        rotation_tradeoffs = [
            text for text in planning.warnings if text.startswith("明确下一餐规划中，")
        ]
        if rotation_tradeoffs:
            facts["next_meal_tradeoff"] = "\n".join(rotation_tradeoffs)
        if state.last_flavor_retraction is not None:
            facts["flavor_retraction"] = retraction_copy(state.last_flavor_retraction)
        facts["catalog"] = (
            "本餐菜品均来自方太菜谱库，原料、步骤与来源可在详情查看。"
            if all(recipe.recipe_id in self.catalog.recipes for recipe in planning.recipes)
            else "库内菜品来自方太菜谱库；本地新生成方案另行标明来源与待试做边界。"
        )
        facts["nutrition"] = (
            "营养说明基于食材和做法作定性分析，未计算热量、蛋白质、糖或钠的精确含量。"
        )
        operation_facts = (
            state_change_facts(intent, previous_state, state) if previous_state is not None else {}
        )
        if "operation" in operation_facts:
            facts["opening"] = operation_facts.pop("operation")
        elif "restore" in operation_facts or "constraint_restore" in operation_facts:
            facts["opening"] = "已按你的要求完成恢复。"
        facts.update(operation_facts)
        warnings = list(planning.warnings)
        if self.experiment_cross_meal_rotation and state.recent_recommendations:
            warnings.append("仅按同一用户最近已推荐菜单做受约束轮换参考；不表示已食用，不保证完全不重复或营养效果。")
        for recipe in chosen:
            warnings.extend(self.health_tool.evaluate(recipe, constraints).warnings)
        warnings.append("缺少份数与完整营养数据，尚不支持逐人定量摄入或健康效果判断。")
        if constraints.people > 1:
            warnings.append("当前按共享菜单聚合已提供的限制；其他用餐者未提供的健康信息仍未知。")
        active_count = effective_attendee_count(state.diners)
        if active_count < constraints.people:
            warnings.append(
                f"已记录 {active_count} 位具体用餐者；其余 {constraints.people - active_count} 位的"
                "个人限制未知。"
            )
        planning_finished = perf_counter()
        source = getattr(self.llm, "explanation_source", "deepseek_verified_facts")
        try:
            selected = await self.llm.explain(facts)
            if not selected or any(key not in facts for key in selected):
                raise ValueError("Unknown explanation fact")
            selected = list(dict.fromkeys(required_fact_ids(intent, facts) + selected))
            reason = "\n".join(facts[key] for key in selected)
        except (LLMUnavailable, LLMOutputError, ValueError):
            source = "verified_template"
            reason = "\n".join(facts.values())
            warnings.append("模型解释暂不可用，已使用同一份验证事实生成说明。")
        if state.last_scene_retractions:
            reason = scene_retraction_copy(state) + "\n" + reason
        if state.last_direct_flavor_retractions:
            reason = direct_flavor_retraction_copy(state) + "\n" + reason
        return ChatResult(
            status="ok", menu=menu, reason=reason,
            constraints=self._constraints_text(
                constraints, state.confirmed_fields, state.diners
            ), conversation_state=state,
            replacement_suggestions=suggestions, warnings=list(dict.fromkeys(warnings)),
            nutrition_analysis=nutrition,
            diner_suitability=suitability,
            tool_calls=events, explanation_source=source,
            timings_ms={
                "retrieval_rules_planning": round((planning_finished - started) * 1000, 2),
                "explanation": round((perf_counter() - planning_finished) * 1000, 2),
            },
        )
