"""Track explicit meal context separately from legacy numeric defaults."""

import re
from collections.abc import Sequence
from typing import Literal

from app.domain.models import ClarificationQuestion, Intent, SessionState

REQUIRED_FIELDS: tuple[Literal["people", "meal_type", "restrictions"], ...] = (
    "people", "meal_type", "restrictions",
)
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


def revoke_asserted_context(message: str) -> bool:
    """A revoke request may quote the constraint it cancels ("取消刚才'不吃鸡蛋'这一条"),
    so quotes alone do not disqualify it. Still reject questions, conditionals,
    uncertainty, attribution to others and explicit example/reference phrasing,
    which are not current assertions."""
    return not re.search(
        r"[?？]|是否|不确定|可能|也许|听说|他说|她说|据说|"
        r"不是|并非|不一定|能否|" + _CONDITIONAL_MARKERS + "|"
        r"举例|示范|例子|演示|比如说|好比|打个比方",
        message,
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
    meals = {
        name for name in MEAL_TYPES if name in message
        and not re.search(r"(?:不要|别|不按|不安排|取消|撤回)(?:安排|按)?" + re.escape(name), message)
    }
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
        or update.diet_mode is not None
        for update in intent.diner_updates
    )
    if (
        intent.allergies or intent.excluded_ingredients or intent.no_spicy is True
        or intent.diet_mode is not None
        or attributed_restrictions
        or (intent.restrictions_confirmed and (
            intent._context_answer_ack or restriction_acknowledged(message, state)
        ))
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


ALLERGY_RESOLUTION_VERSION = "bounded-owner-term-affirmative-answer-v2"
_ANSWER_SPLIT = re.compile(r"[，,。；;！!\r\n]+")
_QUOTED_OR_HYPOTHETICAL = re.compile(r"如果|假如|例如|比如|[\"'“”‘’「」]")


def _resolution_clause(
    clause: str,
    pending_terms: Sequence[str],
    values: Sequence[str],
    *,
    pending_term: str | None = None,
    owner_names: Sequence[str] = (),
    require_owner: bool = False,
    allow_reference: bool = True,
    allow_bare: bool = False,
) -> set[str]:
    """A complete affirmative mapping proves only its named foods/term/owner.

    Explicit quoted/hypothetical/question/additive clauses are not resolutions.
    Caller decides whether an anaphor/bare answer has an unambiguous target.
    Ingredient knowledge and keeping other pending facts remain service duties.
    """
    if not values or _QUOTED_OR_HYPOTHETICAL.search(clause):
        return set()
    text = re.sub(r"\s+", "", clause).strip("，,。.!！")
    choices = "|".join(re.escape(v) for v in sorted(set(values), key=len, reverse=True))
    foods = rf"(?P<foods>(?:{choices})(?:[、和与及](?:{choices}))*)"
    if allow_bare and not require_owner and re.fullmatch(foods, text):
        return set(re.split(r"[、和与及]", text))
    names = "|".join(re.escape(n) for n in sorted(set(owner_names), key=len, reverse=True))
    if require_owner and not names:
        return set()
    owner = rf"(?P<owner>(?:{names})(?:的|(?:明确)?说(?:的)?|表示)?)" if names else ""
    if owner and not require_owner:
        owner = "(?:" + owner + ")?"
    terms = [pending_term] if pending_term is not None else list(pending_terms)
    subject = (
        "(?:"
        + "|".join(re.escape(term) for term in sorted(set(terms), key=len, reverse=True))
        + ")"
        if terms
        else ""
    )
    if subject and allow_reference:
        subject = "(?:" + subject + ")?"
    if not subject and not allow_reference:
        return set()
    reference = r"(?:(?:原来|之前|刚才|原先)(?:说的)?|说的|这里|这次|此处)?"
    mapping = (
        owner
        + r"(?:过敏的)?"
        + reference
        + subject
        + r"(?:过敏(?:原)?)?(?:具体|准确)?"
        + r"(?:只)?(?:就是|指的是|说的是|对应|是|指)"
        + foods
        + r"(?:过敏)?"
    )
    match = re.fullmatch(mapping, text)
    if match:
        return set(re.split(r"[、和与及]", match["foods"]))
    # A previously unnamed allergy may receive a complete named assertion.
    if not pending_terms and pending_term is None:
        match = re.fullmatch(owner + r"(?:对)?" + foods + r"过敏", text)
        if match:
            return set(re.split(r"[、和与及]", match["foods"]))
    return set()


def explicit_allergy_resolution(
    message: str,
    state: SessionState,
    values: list[str],
    *,
    pending_term: str | None = None,
    owner_names: Sequence[str] = (),
    require_owner: bool = False,
) -> bool:
    """Resolve only a raw affirmative answer for this uncertainty, never all facts."""
    if _QUOTED_OR_HYPOTHETICAL.search(message):
        return False
    proven: set[str] = set()
    for clause in _ANSWER_SPLIT.split(message):
        if clause.strip():
            proven.update(
                _resolution_clause(
                    clause,
                    state.pending_allergy_terms,
                    values,
                    pending_term=pending_term,
                    owner_names=owner_names,
                    require_owner=require_owner,
                    allow_reference=len(state.pending_allergy_terms) <= 1,
                    allow_bare=state.pending_fields == ["allergy"]
                    and len(state.pending_allergy_terms) <= 1,
                )
            )
    return bool(values) and set(values) <= proven


def explicit_self_allergy_facts(message: str, known_values: Sequence[str]) -> list[str]:
    """Bind already extracted foods to a literal self assertion, not attendance.

    This supplements an omitted diner update, never removes the original
    meal-wide safety field or resolves pending/unknown allergens. Reuse the
    bounded complete-clause grammar; other people's facts, quotes, questions
    and hypotheses do not prove ownership. A colon is accepted only following
    an explicit first-person meal header, not arbitrary reported speech.
    """
    if not known_values or not asserted_context(message) or re.search(
        r"例如|比如|举例|示例|参考|讨论|转述", message
    ):
        return []
    proven: set[str] = set()
    for clause in _ANSWER_SPLIT.split(message):
        text = re.sub(r"\s+", "", clause)
        if re.search(r"[：:]", text):
            parts = re.split(r"[：:]", text)
            if len(parts) != 2 or not re.fullmatch(
                r"(?:今天|今晚|本餐|这次)?我们[1-8一二三四五六七八两](?:个)?人"
                r"(?:吃|用)?(?:早餐|午餐|晚餐)", parts[0]
            ):
                continue
            text = parts[1]
        proven.update(_resolution_clause(
            text, (), known_values, owner_names=("我", "本人"),
            require_owner=True, allow_bare=False,
        ))
    return list(dict.fromkeys(value for value in known_values if value in proven))


def allergy_answer_only(
    message: str,
    pending_terms: list[str],
    values: list[str],
    *,
    owner_names: Sequence[str] = (),
    meal_type: str | None = None,
    no_spicy: bool = False,
) -> bool:
    """Complete answers may resume an already requested meal, not a new operation.

    Optional clauses are a finite preserve/continue allowlist. Any unanswered
    question, new meal/count, pause, explanation or other task remains clarify.
    This helper never resolves uncertainty or deletes a restriction itself.
    """
    if not values:
        return False
    if _QUOTED_OR_HYPOTHETICAL.search(message):
        return False
    allowed = {
        "没有其他忌口",
        "无其他忌口",
        "没有其他过敏",
        "没有其他限制",
        "其他要求不变",
        "其余不变",
    }
    if no_spicy:
        allowed.update(
            {"我们都不吃辣", "都不吃辣", "我们都不要辣", "不辣", "不要辣", "原来不辣要求不变"}
        )
    if meal_type is not None:
        allowed.update(
            {
                "继续",
                "继续安排",
                "继续安排刚才那顿",
                "继续安排晚餐" if meal_type == "晚餐" else "继续安排" + meal_type,
                "继续安排刚才那顿" + meal_type,
                "继续安排这顿" + meal_type,
                "继续安排之前那顿" + meal_type,
            }
        )
    proven: set[str] = set()
    for clause in _ANSWER_SPLIT.split(message):
        text = re.sub(r"\s+", "", clause).strip("，,。.!！")
        if not text:
            continue
        foods = _resolution_clause(
            text,
            pending_terms,
            values,
            owner_names=owner_names,
            allow_reference=True,
            allow_bare=True,
        )
        if foods:
            proven.update(foods)
        elif text not in allowed:
            return False
    return set(values) <= proven
