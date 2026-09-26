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
from app.agent.diners import (
    DinerConflict,
    aggregate_constraints,
    apply_diner_updates,
    diner_suitability,
    profile_diner,
)
from app.agent.menu_balance import analyze_menu_balance, balance_summary
from app.agent.planner import MenuPlanner, PlanResult
from app.api.presentation import build_card, recipe_provenance, split_cooking_steps
from app.domain.models import (
    ChatResult,
    ClarificationQuestion,
    Constraints,
    Diner,
    Intent,
    MenuItem,
    Recipe,
    SessionState,
    ToolEvent,
    UserProfile,
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
            intent = await self.llm.parse(message, state, profile)
            parsed = perf_counter()
            previous_ids = list(state.menu_ids)
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

    def _apply_intent(self, state: SessionState, intent: Intent, message: str) -> str | None:
        meal_constraints = state.meal_constraints or state.constraints.model_copy(deep=True)
        state.meal_constraints = meal_constraints
        constraints = meal_constraints
        for update in intent.diner_updates:
            unresolved_diner = self.rules.unresolved_allergies(
                Constraints(allergies=update.allergies)
            )
            if unresolved_diner:
                return (
                    f"{update.diner}的过敏原缺少可靠映射，请明确具体食材："
                    + "、".join(unresolved_diner)
                )
        confirm_from_intent(state, intent, message)
        positive_allergy_text = re.sub(
            r"(?:没有|没|无)(?:任何|其他|额外)?(?:食物|食材)?过敏(?:史)?|不(?:会)?过敏|非过敏", "", message
        )
        attributed_allergies = [
            allergy for update in intent.diner_updates for allergy in update.allergies
        ]
        if "过敏" in positive_allergy_text and not intent.allergies and not attributed_allergies:
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
        previous_active_count = sum(diner.attendance for diner in state.diners)
        try:
            state.diners = apply_diner_updates(
                state.diners, intent.diner_updates, session_id=state.session_id
            )
        except DinerConflict as error:
            state.constraints = aggregate_constraints(constraints, state.diners)
            return str(error)
        for name in ("dish_count", "soup_count", "people", "max_minutes"):
            value = getattr(intent, name)
            if value is not None:
                setattr(constraints, name, value)
        if intent.dish_count is not None or intent.soup_count is not None:
            state.menu_structure_explicit = True
        attendance_changed = any(
            update.attendance is not None for update in intent.diner_updates
        )
        active_count = sum(diner.attendance for diner in state.diners)
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
        unresolved = self.rules.unresolved_allergies(state.constraints)
        if unresolved:
            return "当前词典无法确认这些过敏原，请明确具体食材：" + "、".join(unresolved)
        if state.constraints.soup_count > state.constraints.dish_count:
            return "汤的数量不能超过总菜数，请明确总共几道，其中几道汤。"
        if intent.action == "clarify" or intent.clarification:
            return intent.clarification or "请补充本餐需要调整的具体要求。"
        questions = missing_questions(state)
        if questions:
            return "规划前还需要确认：" + " ".join(question.prompt for question in questions)
        return None

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
            if details:
                items.append(f"{diner.display_name}：" + "；".join(details))
        active_count = sum(diner.attendance for diner in diners or [])
        if "people" in confirmed and active_count < constraints.people:
            items.append(f"其余 {constraints.people - active_count} 位用餐者的个人限制尚未提供")
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
        menu_balance = analyze_menu_balance(chosen)
        suitability = diner_suitability(chosen, state.diners, self.rules)
        if any(not item.hard_constraints_satisfied for item in suitability):
            raise RuntimeError("Final per-diner validation failed")
        state.menu_ids = chosen_ids
        state.menu_valid = True
        state.pending_clarification = None
        state.pending_fields = []
        facts = {
            "catalog": "本餐菜品均来自方太菜谱库，可通过 recipe_id 查到具体食材与步骤。",
            "constraints": "已按当前已知过敏、排除食材与明确要求执行规则检查。",
            "balance": balance_summary(menu_balance),
            "nutrition": "营养说明基于食材和做法作定性分析，未计算热量、蛋白质、糖或钠的精确含量。",
        }
        active_diners = [diner for diner in state.diners if diner.attendance]
        if len(active_diners) > 1:
            diner_facts = []
            for diner in active_diners:
                hard = []
                if diner.allergies:
                    hard.append("过敏=" + "、".join(diner.allergies))
                if diner.excluded_ingredients:
                    hard.append("不吃=" + "、".join(diner.excluded_ingredients))
                if diner.no_spicy:
                    hard.append("不吃辣")
                diner_facts.append(
                    f"{diner.display_name}（{'；'.join(hard) if hard else '未提供个人硬约束'}）"
                )
            facts["diners"] = (
                "逐人适配：" + "、".join(diner_facts)
                + "的已知硬约束均已按共享菜单核对；未提供信息保持未知。"
            )
        if previous_ids:
            kept = sum(old == new for old, new in zip(previous_ids, chosen_ids))
            facts["changes"] = (
                f"与上一版相比，保留原位置上的 {kept} 道菜，其余按本轮要求重新选择。"
            )
        for item in menu:
            if item.nutrition_notes:
                facts[f"dish_{item.slot}"] = f"{item.name}：{item.nutrition_notes[0]}"
        warnings = list(planning.warnings)
        for recipe in chosen:
            warnings.extend(self.health_tool.evaluate(recipe, constraints).warnings)
        warnings.append("缺少份数与完整营养数据，尚不支持逐人定量摄入或健康效果判断。")
        if constraints.people > 1:
            warnings.append("当前按共享菜单聚合已提供的限制；其他用餐者未提供的健康信息仍未知。")
        active_count = sum(diner.attendance for diner in state.diners)
        if active_count < constraints.people:
            warnings.append(
                f"已记录 {active_count} 位具体用餐者；其余 {constraints.people - active_count} 位的"
                "个人限制未知。"
            )
        planning_finished = perf_counter()
        source = "deepseek_verified_facts"
        try:
            selected = await self.llm.explain(facts)
            if not selected or any(key not in facts for key in selected):
                raise ValueError("Unknown explanation fact")
            required_facts = ["catalog", "constraints", "balance"]
            if "diners" in facts:
                required_facts.append("diners")
            selected = list(dict.fromkeys(required_facts + selected + ["nutrition"]))
            reason = "\n".join(facts[key] for key in selected)
        except (LLMUnavailable, LLMOutputError, ValueError):
            source = "verified_template"
            reason = "\n".join(facts.values())
            warnings.append("模型解释暂不可用，已使用同一份验证事实生成说明。")
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
