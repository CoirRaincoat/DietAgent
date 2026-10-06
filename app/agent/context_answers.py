"""Ground finite replies to the agent's pending initial meal-context questions.

Not a general parser or a source of defaults. A complete literal answer may
resume only the initial pending plan, never a previous menu or local edit.
"""

import re

from app.agent.clarification import REQUIRED_FIELDS, missing_questions, restriction_acknowledged
from app.agent.response_copy import clarification_copy
from app.domain.models import Intent, SessionState

VERSION = "bounded-initial-context-answer-v2"
_MEALS = "早餐|午餐|晚餐|夜宵|下午茶"
_PEOPLE = re.compile(
    rf"(?:我们|本餐|这餐)?(?:一共|总共|共)?(?P<count>[1-8一二两三四五六七八])"
    rf"(?:个?人|位)(?:吃)?(?:(?:的)?(?P<meal>{_MEALS}))?"
)
_MEAL = re.compile(rf"(?:吃|安排)?(?P<meal>{_MEALS})")
_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
_NON_ASSERTION = re.compile(r"[?？\"'“”‘’「」`]|如果|假如|例如|比如|引用|他说|她说")


def grounded_context_answer(message: str, state: SessionState) -> Intent | None:
    """Return only literal context facts, bound to an unanswered initial plan.

    Unknown allergy/diet/composition/flavor/time questions are never resolved
    here. Mixed new requirements stay on the normal additive/safety path.
    """
    if (
        not state.pending_plan
        or state.menu_ids
        or not state.pending_fields
        or not set(state.pending_fields) <= set(REQUIRED_FIELDS)
        or state.pending_flavor_resolution is not None
        or state.pending_method_tradeoff is not None
        or state.pending_clarification != clarification_copy(missing_questions(state))
        or _NON_ASSERTION.search(message)
    ):
        return None
    clauses = [
        clause.strip() for clause in re.split(r"[，,。；;！!\r\n]+", message) if clause.strip()
    ]
    if not clauses:
        return None
    people: int | None = None
    meal: str | None = None
    acknowledgments: list[str] = []
    for clause in clauses:
        person = _PEOPLE.fullmatch(clause)
        meal_only = _MEAL.fullmatch(clause)
        if person:
            raw = person.group("count")
            count = _NUMBERS[raw] if raw in _NUMBERS else int(raw)
            if people is not None and people != count:
                return None
            people = count
            supplied_meal = person.group("meal")
        elif meal_only:
            supplied_meal = meal_only.group("meal")
        else:
            acknowledgments.append(clause)
            continue
        if supplied_meal:
            if meal is not None and meal != supplied_meal:
                return None
            meal = supplied_meal
    # A short '没有' is unambiguous only after this literal answer has filled
    # the other context questions. The original state is never mutated here.
    context = state.model_copy(deep=True)
    confirmed = set(context.confirmed_fields)
    if people is not None:
        confirmed.add("people")
    if meal is not None:
        confirmed.add("meal_type")
    context.confirmed_fields = [field for field in REQUIRED_FIELDS if field in confirmed]
    context.pending_fields = [field for field in REQUIRED_FIELDS if field not in confirmed]
    if any(not restriction_acknowledged(clause, context) for clause in acknowledgments):
        return None
    answer = Intent(people=people, meal_type=meal, restrictions_confirmed=bool(acknowledgments))
    answer._context_answer_ack = bool(acknowledgments)
    return answer
