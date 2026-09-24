"""Track explicit meal context separately from legacy numeric defaults."""

import re

from app.domain.models import ClarificationQuestion, Intent, SessionState

REQUIRED_FIELDS = ("people", "meal_type", "restrictions")
MEAL_TYPES = {"早餐", "午餐", "晚餐", "夜宵", "下午茶"}


def restriction_acknowledged(message: str, state: SessionState) -> bool:
    """A generic 'whatever' never confirms the absence of dietary restrictions."""
    affirmative = (
        r"(?:我|我们|大家)?(?:本餐|这餐|目前|现在|暂时)?(?:"
        r"(?:没有|无|没什么|没有其他|无其他|没有额外|无额外)(?:的)?忌口"
        r"|(?:没有|无)(?:任何|其他)?(?:食物|食材)?过敏"
        r"|(?:按照|按|沿用|保留)(?:已有|原有|我的|用户)?(?:档案|画像)(?:中|里)?(?:的)?(?:忌口|限制)?"
        r"|(?:没有|无)(?:其他|额外)限制|(?:什么)?都能吃)(?:了)?"
    )
    if any(re.fullmatch(affirmative, clause.strip()) for clause in re.split(r"[，,。；;\n]", message)):
        return True
    missing = set(REQUIRED_FIELDS) - set(state.confirmed_fields)
    return (
        missing == {"restrictions"}
        and state.pending_fields == ["restrictions"]
        and message.strip(" ，。！!") in {"没有", "无", "没有其他"}
    )


def confirm_from_intent(state: SessionState, intent: Intent, message: str) -> None:
    confirmed = set(state.confirmed_fields)
    if intent.people is not None:
        confirmed.add("people")
    if intent.meal_type in MEAL_TYPES:
        confirmed.add("meal_type")
    if (
        intent.allergies or intent.excluded_ingredients or intent.no_spicy is True
        or (intent.restrictions_confirmed and restriction_acknowledged(message, state))
    ):
        confirmed.add("restrictions")
    state.confirmed_fields = [name for name in REQUIRED_FIELDS if name in confirmed]


def missing_questions(state: SessionState) -> list[ClarificationQuestion]:
    prompts = {
        "people": ClarificationQuestion(
            field="people", prompt="这餐几个人吃？", options=["1人", "2人", "3人", "4人"],
        ),
        "meal_type": ClarificationQuestion(
            field="meal_type", prompt="安排哪一餐？", options=["早餐", "午餐", "晚餐"],
        ),
        "restrictions": ClarificationQuestion(
            field="restrictions", prompt="有什么过敏食材或忌口？没有也请说明。",
            options=["没有其他忌口", "按档案忌口", "补充忌口食材"],
        ),
    }
    return [prompts[field] for field in REQUIRED_FIELDS if field not in state.confirmed_fields]


def explicit_allergy_resolution(message: str, state: SessionState, values: list[str]) -> bool:
    """Additional allergies cannot resolve an earlier unknown ingredient."""
    if re.search(r"另外|此外|还有|也过敏|还过敏|新增|再加|是否|吗|[?？]", message):
        return False
    if re.search(r"具体|指的是|原来|说的是|分别是|对应|就是|准确", message):
        return True
    if any(term in message and "是" in message for term in state.pending_allergy_terms):
        return True
    return (
        state.pending_fields == ["allergy"]
        and message.strip(" ，。！!") in values
    )
