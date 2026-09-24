"""Stateful retrieval-grounded meal planning with independently validated output."""

import asyncio
import hashlib
import re
from time import perf_counter
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.agent.clarification import (
    confirm_from_intent,
    explicit_allergy_resolution,
    missing_questions,
)
from app.agent.planner import MenuPlanner, PlanResult
from app.agent.response_copy import render_reason, verified_facts
from app.api.presentation import build_card, recipe_provenance, split_cooking_steps
from app.domain.models import (
    ChatResult,
    ClarificationQuestion,
    Constraints,
    Intent,
    MenuItem,
    Recipe,
    SessionState,
    ToolEvent,
)
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM, LLMOutputError, LLMUnavailable
from app.infrastructure.sessions import SessionStore
from app.retrieval.keyword import KeywordRetriever
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
    def __init__(self, catalog: DataCatalog, store: SessionStore, llm: BaseLLM):
        self.catalog = catalog
        self.store = store
        self.llm = llm
        self.rules = RuleEngine()
        self.retriever = KeywordRetriever(catalog.recipes.values())
        self.planner = MenuPlanner(self.rules)
        self._locks: dict[str, asyncio.Lock] = {}
        self.health_tool = HealthCheckTool(self.rules)
        self.tools = ToolRegistry()
        self.tools.register("recipe_search", RecipeSearchTool(self.retriever))
        self.tools.register("health_check", self.health_tool)
        self.tools.register("menu_modify", MenuModifyTool(self.planner))
        self.tools.register("nutrition_analysis", NutritionAnalysisTool())

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
        lock = self._locks.setdefault(session_id, asyncio.Lock())
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
                state = SessionState(
                    session_id=session_id, user_id=user_id,
                    confirmed_fields=["restrictions"] if profile.allergies else [],
                    constraints=Constraints(
                        allergies=list(profile.allergies),
                        preferences=list(profile.preferences),
                        health_goals=list(profile.health_goals),
                    ),
                )
                expected_revision = None
            else:
                expected_revision = state.revision
            started = perf_counter()
            try:
                intent = await self.llm.parse(message, state, profile)
            except LLMOutputError:
                parsed = perf_counter()
                return self._complete_preserved_clarification(
                    state=state,
                    expected_revision=expected_revision,
                    message=message,
                    reason=(
                        "我没有完全理解这次调整。请确认你是要增加忌口、"
                        "替换某道菜，还是重新安排整套菜单？"
                    ),
                    options=["增加忌口", "替换某道菜", "重新安排整套菜单"],
                    started=started,
                    parsed=parsed,
                    request_id=request_id,
                    request_hash=request_hash,
                )
            parsed = perf_counter()
            previous_ids = list(state.menu_ids)
            conflict = self._intent_conflict(state, intent)
            if conflict:
                return self._complete_preserved_clarification(
                    state=state,
                    expected_revision=expected_revision,
                    message=message,
                    reason=conflict,
                    options=["调整总菜数", "调整汤的数量"],
                    started=started,
                    parsed=parsed,
                    request_id=request_id,
                    request_hash=request_hash,
                )
            if intent.action == "reject":
                # Save explicit rejection before clarification/planning can fail.
                state.rejected_recipe_ids = _merge(state.rejected_recipe_ids, previous_ids)
            issue = self._apply_intent(state, intent, message)
            state.revision += 1
            state.last_message = message
            state.menu_valid = False
            state.pending_clarification = issue
            state.history = (state.history + [{"role": "user", "content": message}])[-12:]
            # Valid new restrictions survive planning failures and process restarts.
            self.store.save(state, expected_revision)
            events: list[ToolEvent] = []
            if issue:
                result = self._unresolved(state, issue, "clarification_required", events)
            else:
                result = await self._plan(state, intent, previous_ids, events)
            result.timings_ms.update(
                parse=round((parsed - started) * 1000, 2),
                total=round((perf_counter() - started) * 1000, 2),
            )
            state.history = (
                state.history + [{"role": "assistant", "content": result.reason}]
            )[-12:]
            self.store.complete(result, state.revision, request_id, request_hash)
            return result

    @staticmethod
    def _intent_conflict(state: SessionState, intent: Intent) -> str | None:
        """Detect count conflicts before applying any part of the new intent."""
        dish_count = intent.dish_count or state.constraints.dish_count
        soup_count = (
            intent.soup_count if intent.soup_count is not None else state.constraints.soup_count
        )
        if soup_count > dish_count:
            return (
                f"你要求总共 {dish_count} 道菜、其中 {soup_count} 道汤，但汤数不能超过总菜数。"
                "请确认要调整总菜数，还是减少汤的数量？"
            )
        return None

    def _complete_preserved_clarification(
        self,
        state: SessionState,
        expected_revision: int | None,
        message: str,
        reason: str,
        options: list[str],
        started: float,
        parsed: float,
        request_id: str | None,
        request_hash: str,
    ) -> ChatResult:
        """Record a clarification turn while preserving confirmed constraints and menu."""
        state.revision += 1
        state.last_message = message
        state.pending_clarification = reason
        state.pending_fields = ["request"]
        state.history = (state.history + [{"role": "user", "content": message}])[-12:]
        events: list[ToolEvent] = []
        menu = []
        nutrition = None
        if state.menu_valid:
            recipes = [
                self.catalog.recipes[recipe_id]
                for recipe_id in state.menu_ids
                if recipe_id in self.catalog.recipes
            ]
            if len(recipes) == len(state.menu_ids):
                menu = [
                    self._item(recipe, slot, state.constraints, events)
                    for slot, recipe in enumerate(recipes, start=1)
                ]
                nutrition = self.tools.call(
                    "nutrition_analysis", events, recipes=recipes, constraints=state.constraints
                )
        result = ChatResult(
            status="clarification_required",
            menu=menu,
            reason=reason,
            constraints=self._constraints_text(state.constraints, state.confirmed_fields),
            conversation_state=state,
            warnings=["已保留上一版有效菜单和已确认条件，本轮尚未应用。"] if menu else [],
            tool_calls=events,
            clarification_questions=[
                ClarificationQuestion(field="request", prompt=reason, options=options)
            ],
            nutrition_analysis=nutrition,
            timings_ms={
                "parse": round((parsed - started) * 1000, 2),
                "total": round((perf_counter() - started) * 1000, 2),
            },
        )
        state.history = (
            state.history + [{"role": "assistant", "content": result.reason}]
        )[-12:]
        self.store.save(state, expected_revision)
        self.store.complete(result, state.revision, request_id, request_hash)
        return result

    def _apply_intent(self, state: SessionState, intent: Intent, message: str) -> str | None:
        constraints = state.constraints
        confirm_from_intent(state, intent, message)
        positive_allergy_text = re.sub(
            r"(?:没有|没|无)(?:任何|其他|额外)?(?:食物|食材)?过敏(?:史)?|不(?:会)?过敏|非过敏", "", message
        )
        if "过敏" in positive_allergy_text and not intent.allergies:
            state.pending_allergy = True
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
                and explicit_allergy_resolution(message, state, replacements)
            ):
                state.pending_allergy_terms.remove(pending)
                resolved_values.extend(replacements)
        incoming = _merge(intent.allergies, resolved_values)
        incoming_unknown = self.rules.unresolved_allergies(Constraints(allergies=incoming))
        known_incoming = [term for term in incoming if term not in incoming_unknown]
        if known_incoming and not incoming_unknown and not state.pending_allergy_terms:
            # An unnamed question may be answered with a named allergen, but
            # an additive phrase leaves the previous uncertainty unresolved.
            if not re.search(r"另外|此外|还有|也过敏|还过敏|新增|再加", message):
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
        for name in ("dish_count", "soup_count", "people", "max_minutes"):
            value = getattr(intent, name)
            if value is not None:
                setattr(constraints, name, value)
        if intent.meal_type:
            if intent.meal_type not in {"早餐", "午餐", "晚餐", "夜宵", "下午茶"}:
                return "请说明要安排早餐、午餐、晚餐还是加餐。"
            constraints.meal_type = intent.meal_type
        if intent.inventory is not None:
            constraints.inventory = intent.inventory
        if intent.no_spicy is True:
            constraints.no_spicy = True
        elif intent.no_spicy is False and constraints.no_spicy:
            return "当前会话保留了不吃辣的要求；如要撤销，请另开会话明确本餐要求。"
        if intent.clear_time_limit:
            if re.search(
                r"时间(?:不限制|不限|不作限制)|不限时间|不限制.*时间|取消.*时间|不限定时间|(?:去掉|不设|不要|不用|没有).*时间限制|不赶时间",
                message,
            ):
                constraints.max_minutes = None
            else:
                return "请明确是否取消总耗时限制。"
        if state.pending_allergy:
            details = "（请逐一明确：" + "、".join(state.pending_allergy_terms) + "）" if state.pending_allergy_terms else ""
            return "尚未明确具体过敏食材，请先补充过敏信息" + details + "；此前菜单暂不作为可用建议。"
        unresolved = self.rules.unresolved_allergies(constraints)
        if unresolved:
            return "当前词典无法确认这些过敏原，请明确具体食材：" + "、".join(unresolved)
        if intent.action == "clarify" or intent.clarification:
            return intent.clarification or "请补充本餐需要调整的具体要求。"
        questions = missing_questions(state)
        if questions:
            return "规划前还需要确认：" + " ".join(question.prompt for question in questions)
        return None

    def _constraints_text(self, constraints: Constraints, confirmed: list[str]) -> list[str]:
        people = f"{constraints.people} 人" if "people" in confirmed else "人数待确认"
        meal = constraints.meal_type if "meal_type" in confirmed else "餐次待确认"
        items = [f"{people}，{meal}，共 {constraints.dish_count} 道（含汤）"]
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
        return items

    def _unresolved(
        self, state: SessionState, reason: str, status: str, events: list[ToolEvent]
    ) -> ChatResult:
        state.menu_valid = False
        state.pending_clarification = reason if status == "clarification_required" else None
        questions = []
        if status == "clarification_required":
            questions = missing_questions(state)
            if state.pending_allergy or self.rules.unresolved_allergies(state.constraints):
                questions = [question for question in questions if question.field != "restrictions"]
                questions.insert(0, ClarificationQuestion(field="allergy", prompt=reason))
            elif not questions and state.constraints.max_minutes is not None:
                questions = [ClarificationQuestion(
                    field="time_limit", prompt=reason, options=["取消时间限制", "暂不规划"],
                )]
            elif not questions:
                questions = [ClarificationQuestion(field="request", prompt=reason)]
        state.pending_fields = [question.field for question in questions]
        return ChatResult(
            status=status, reason=reason,
            constraints=self._constraints_text(state.constraints, state.confirmed_fields),
            conversation_state=state, tool_calls=events, clarification_questions=questions,
            warnings=["旧菜单尚未通过当前约束校验，不作为本轮推荐。"],
        )

    def _item(
        self, recipe: Recipe, slot: int, constraints: Constraints, events: list[ToolEvent]
    ) -> MenuItem:
        decision = self.health_tool.evaluate(recipe, constraints)
        if not decision.allowed or recipe.recipe_id not in self.catalog.recipes:
            raise RuntimeError("Final recipe validation failed")
        return MenuItem(
            slot=slot, recipe_id=recipe.recipe_id, name=recipe.name,
            ingredients=[ingredient.name for ingredient in recipe.ingredients],
            steps=recipe.steps, reasons=decision.reasons,
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
        events: list[ToolEvent],
    ) -> ChatResult:
        started = perf_counter()
        constraints = state.constraints
        rejected_ids = set(state.rejected_recipe_ids)
        rejected_names = {
            compact(self.catalog.recipes[key].name)
            for key in rejected_ids if key in self.catalog.recipes
        }
        # Multiple catalog rows may represent the same named dish. Do not
        # reintroduce a rejected dish through another recipe ID or a suggestion.
        rejected_ids.update(
            recipe.recipe_id for recipe in self.catalog.recipes.values()
            if compact(recipe.name) in rejected_names
        )
        if constraints.max_minutes is not None:
            return self._unresolved(
                state,
                "菜谱缺少可验证的整餐总耗时，无法保证该时间上限。是否取消时间限制，先按其他要求规划？",
                "clarification_required", events,
            )
        current = [self.catalog.recipes[key] for key in previous_ids if key in self.catalog.recipes]
        replace_slot = intent.replace_slot
        if intent.action == "replace":
            if not current:
                return self._unresolved(
                    state, "还没有可替换的菜单，请先推荐一餐。", "clarification_required", events
                )
            if replace_slot is None and intent.replace_name:
                matches = [i + 1 for i, recipe in enumerate(current) if (
                    recipe.name == intent.replace_name
                    or (intent.replace_name in {"汤", "汤菜"} and "soup" in recipe.categories)
                )]
                if len(matches) == 1:
                    replace_slot = matches[0]
            if replace_slot is None or replace_slot > len(current):
                return self._unresolved(
                    state, "请说明要换第几道菜。", "clarification_required", events
                )
        if intent.action == "explain":
            verified_current = self.tools.call(
                "health_check", events, recipes=current, constraints=constraints,
            )
            if (
                len(current) != constraints.dish_count
                or sum("soup" in recipe.categories for recipe in current) != constraints.soup_count
                or len(verified_current) != len(current)
                or any(recipe.recipe_id in rejected_ids for recipe in current)
            ):
                return self._unresolved(
                    state, "当前没有满足已知约束的完整菜单，请先规划本餐。",
                    "clarification_required", events,
                )
            planning = PlanResult(recipes=current, changes=[], warnings=[], failure=None)
            safe = []
        else:
            query_terms = list(dict.fromkeys(intent.query_terms + constraints.preferred_ingredients))
            candidates = self.tools.call(
                "recipe_search", events, query_terms=query_terms, constraints=constraints, limit=None
            )
            safe = self.tools.call(
                "health_check", events, recipes=candidates, constraints=constraints
            )
            safe = [recipe for recipe in safe if recipe.recipe_id not in rejected_ids]
            planning = self.tools.call(
                "menu_modify", events, candidates=safe, constraints=constraints, current=current,
                replace_slot=replace_slot,
                reject_ids=rejected_ids,
            )
        if planning.failure:
            return self._unresolved(state, planning.failure, "no_feasible_menu", events)
        chosen = planning.recipes
        if (
            len(chosen) != constraints.dish_count
            or len({recipe.recipe_id for recipe in chosen}) != len(chosen)
            or sum("soup" in recipe.categories for recipe in chosen) != constraints.soup_count
            or any(recipe.recipe_id in rejected_ids for recipe in chosen)
        ):
            raise RuntimeError("Final menu structure validation failed")
        menu = [self._item(recipe, i + 1, constraints, events) for i, recipe in enumerate(chosen)]
        chosen_ids = [recipe.recipe_id for recipe in chosen]
        suggestions: list[MenuItem] = []
        # Same-role alternatives preserve the known structural properties of slot 1.
        if chosen:
            for candidate in safe:
                if candidate.recipe_id in chosen_ids or compact(candidate.name) in {
                    compact(recipe.name) for recipe in chosen
                }:
                    continue
                if set(candidate.categories) != set(chosen[0].categories):
                    continue
                suggestion = self._item(candidate, 1, constraints, events)
                suggestion.replacement_reason = "与第 1 道菜类别一致，已核对当前已知食材限制；份量仍需确认。"
                suggestions.append(suggestion)
                if len(suggestions) == 2:
                    break
        nutrition = self.tools.call(
            "nutrition_analysis", events, recipes=chosen, constraints=constraints,
        )
        state.menu_ids = chosen_ids
        state.menu_valid = True
        state.pending_clarification = None
        state.pending_fields = []
        facts = verified_facts(menu)
        warnings = list(planning.warnings)
        for recipe in chosen:
            warnings.extend(self.health_tool.evaluate(recipe, constraints).warnings)
        if constraints.people > 1:
            warnings.append("当前按共享菜单聚合已提供的限制；其他用餐者未提供的健康信息仍未知。")
        planning_finished = perf_counter()
        source = "deepseek_verified_facts"
        try:
            selected = await self.llm.explain(facts)
            if not selected or any(key not in facts for key in selected):
                raise ValueError("Unknown explanation fact")
        except (LLMUnavailable, LLMOutputError, ValueError):
            source = "verified_template"
            selected = list(facts)
            warnings.append("模型解释暂不可用，已使用同一份验证事实生成说明。")
        reason = render_reason(
            intent=intent,
            menu=menu,
            previous_ids=previous_ids,
            changes=planning.changes,
            selected_fact_ids=selected,
            revision=state.revision,
        )
        return ChatResult(
            status="ok", menu=menu, reason=reason,
            constraints=self._constraints_text(constraints, state.confirmed_fields), conversation_state=state,
            replacement_suggestions=suggestions, warnings=list(dict.fromkeys(warnings)),
            nutrition_analysis=nutrition,
            tool_calls=events, explanation_source=source,
            timings_ms={
                "retrieval_rules_planning": round((planning_finished - started) * 1000, 2),
                "explanation": round((perf_counter() - planning_finished) * 1000, 2),
            },
        )
