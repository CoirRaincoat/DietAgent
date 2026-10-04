"""Track explicit meal context separately from legacy numeric defaults."""

import re

from app.domain.models import ClarificationQuestion, Intent, SessionState

REQUIRED_FIELDS = ("people", "meal_type", "restrictions")
MEAL_TYPES = {"早餐", "午餐", "晚餐", "夜宵", "下午茶"}


_CONDITIONAL_MARKERS = r"如果|假如|假设|要是|倘若|万一|一旦|若|则|的话"


def conditional_context(message: str) -> bool:
    """Whether the message states a condition/hypothesis instead of a fact."""
    return bool(re.search(_CONDITIONAL_MARKERS, message))


def asserted_context(message: str) -> bool:
    """Do not recover affirmative facts from questions, quotes or hypotheticals."""
    return not re.search(
        r"[?？\"'“”‘’]|是否|不确定|可能|也许|听说|他说|她说|据说|"
        r"不是|并非|不一定|能否|" + _CONDITIONAL_MARKERS, message
    )


def _describes_individual_member(message: str, count_terms: list[str]) -> bool:
    """A lone Chinese "一个人/一人" is a per-member description, not the total.

    Arabic "1人" and Chinese counts >=2 are unambiguous totals. The Chinese
    numeral "一" only means a one-person party when the speaker asserts being
    alone ("就一个人 / 我一个人 / 只有我一个人 / 独自一个人"); otherwise it
    describes a member's attribute or distribution.
    """
    if not count_terms or count_terms[0] != "一":
        return False
    return not re.search(
        r"(?:就|只|只有|只是|独自|单独|自己)(?:[^，。；;！!？?\n]{0,4})一(?:个)?人"
        r"|(?:我|本人)一(?:个)?人",
        message,
    )


def recover_explicit_meal_context(intent: Intent, message: str) -> Intent:
    """Recover only bounded, unambiguous literal facts after a valid parse.

    This applies equally to planning and clarification, never to damaged model
    output. Optional intent fields remain unknown when the message is ambiguous.
    """
    if not asserted_context(message):
        return intent
    if re.search(r"(?:不要|别|取消|不按|不安排)(?:安排|按)?[1-8一二两三四五六七八](?:个)?人", message):
        return intent
    numbers = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
               "五": 5, "六": 6, "七": 7, "八": 8}
    count_terms = re.findall(
        r"(?<![\d一二三四五六七八九十])([1-8一二两三四五六七八])(?:个)?人", message
    )
    found = {
        int(token) if token.isdigit() else numbers[token] for token in count_terms
    }
    # A relative count ("me and three other people") is not the meal total.
    # Leave that field unknown rather than adding people with ad hoc arithmetic.
    relative_count = re.search(
        r"(?:另外|其他|其余|另有|还有|再加|加上|再来)\s*"
        r"[1-8一二两三四五六七八](?:个)?人", message
    )
    changes = {}
    if (
        intent.people is None
        and len(found) == 1
        and not relative_count
        and not _describes_individual_member(message, count_terms)
    ):
        changes["people"] = found.pop()
    meals = {name for name in MEAL_TYPES if name in message}
    if intent.meal_type is None and len(meals) == 1:
        changes["meal_type"] = meals.pop()
    return intent.model_copy(update=changes) if changes else intent


def restriction_acknowledged(message: str, state: SessionState) -> bool:
    """A generic 'whatever' never confirms the absence of dietary restrictions."""
    if not asserted_context(message):
        return False
    affirmative = (
        r"(?:我|我们|大家)?(?:本餐|这餐|目前|现在|暂时)?(?:"
        r"(?:没有|无|没什么|没有其他|无其他|没有额外|无额外)(?:的)?忌口"
        r"|(?:没有|无)(?:任何|其他)?(?:食物|食材)?过敏"
        r"|(?:按照|按|沿用|保留)(?:已有|原有|我的|用户)?(?:档案|画像)(?:中|里)?(?:的)?(?:忌口|限制)?"
        r"|(?:没有|无)(?:其他|额外)限制|(?:什么)?都能吃)(?:了)?"
    )
    compound = (
        r"(?:我|我们|大家)?(?:本餐|这餐|目前|现在|暂时)?(?:没有|无)"
        r"(?:食物|食材)?过敏(?:或|和|及|也没有|也无)(?:其他|额外)?忌口"
    )
    if any(re.fullmatch(compound, clause.strip()) for clause in re.split(r"[，,。；;\n]", message)):
        return True
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
    attributed_restrictions = any(
        update.allergies
        or update.excluded_ingredients
        or update.no_spicy is True
        for update in intent.diner_updates
    )
    if (
        intent.allergies or intent.excluded_ingredients or intent.no_spicy is True
        or attributed_restrictions
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
    if re.search(r"另外|此外|还有|也过敏|还过敏|新增|再加|是否|吗|忘了|不清楚|不确定|记不清|也许|可能|[?？]", message):
        return False
    if re.search(r"具体|指的是|原来|说的是|分别是|对应|就是|准确", message):
        return True
    if any(term in message and "是" in message for term in state.pending_allergy_terms):
        return True
    answer = message.strip(" ，。！!")
    if state.pending_fields == ["allergy"] and values:
        names = "|".join(re.escape(value) for value in values)
        if re.fullmatch(rf"(?:我)?(?:对)?(?:{names})(?:(?:和|、)(?:{names}))*过敏", answer):
            return True
    return (
        state.pending_fields == ["allergy"]
        and answer in values
    )
