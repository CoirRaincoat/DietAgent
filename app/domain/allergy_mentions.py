"""Shared conservative guard for omitted allergy information at both entry points."""

import re
from dataclasses import dataclass

from app.domain.models import Intent, SessionState

_NEGATIVE_ALLERGY = re.compile(
    r"(?:没有|没|无)(?:任何|其他|额外)?(?:食物|食材)?过敏(?:史)?|不(?:会)?过敏|非过敏"
)

ALLERGY_MENTION_VERSION = "per-assertion-owner-coverage-v2"
_IDENTITIES = {
    "我爸": "爸爸",
    "父亲": "爸爸",
    "老爸": "爸爸",
    "我妈": "妈妈",
    "母亲": "妈妈",
    "老妈": "妈妈",
    "我": "用户",
    "本人": "用户",
}
_RELATIVES = (
    "爸爸",
    "妈妈",
    "朋友",
    "爷爷",
    "奶奶",
    "外公",
    "外婆",
    "孩子",
    "儿子",
    "女儿",
    "用户",
)
_CONNECTORS = re.compile(r"[，,。；;！!\r\n]+|但(?:是)?|而(?:且)?|另外|此外|同时|并且")
_NON_ASSERTION = re.compile(r"如果|假如|例如|比如|是否|[\"'“”‘’「」]")
_GRAMMAR = re.compile(
    r"(?:指的是|说的是|这里|这次|此处|过敏原|过敏|准确|具体|就是|只指|对应|原来|之前|刚才|说的|新增|对|的|和|与|及|、|都|还|也|有|是|指|只)+"
)


@dataclass(frozen=True)
class UncoveredAllergyMention:
    owner: str | None
    raw_clause: str
    active: bool = True


def _identity(name: str) -> str:
    return _IDENTITIES.get(name, name)


def _evidence(
    intent: Intent, state: SessionState
) -> tuple[dict[str, set[str]], dict[str, list[str]], dict[str, bool], list[str]]:
    """No ingredient inference: literal extracted/persisted facts only."""
    aliases: dict[str, set[str]] = {}
    foods: dict[str, list[str]] = {}
    attendance: dict[str, bool] = {}
    for diner in state.diners:
        owner = _identity(diner.display_name)
        for name in [diner.display_name, *diner.aliases]:
            aliases.setdefault(name, set()).add(owner)
        foods[owner] = []
        attendance[owner] = diner.attendance
    for update in intent.diner_updates:
        matches = {
            owner for name in [update.diner, *update.aliases] for owner in aliases.get(name, set())
        }
        owner = next(iter(matches)) if len(matches) == 1 else _identity(update.diner)
        for name in [update.diner, *update.aliases]:
            aliases.setdefault(name, set()).add(owner)
        foods.setdefault(owner, []).extend(
            [
                *update.allergies,
                *update.allergy_clarifications,
                *(v for values in update.allergy_clarifications.values() for v in values),
            ]
        )
        if update.attendance is not None:
            attendance[owner] = update.attendance
    for name in [*_RELATIVES, *_IDENTITIES]:
        aliases.setdefault(name, {_identity(name)})
    global_foods = [
        *intent.allergies,
        *intent.allergy_clarifications,
        *(v for values in intent.allergy_clarifications.values() for v in values),
    ]
    # Legacy undifferentiated fields are meal-wide hard facts. Do not borrow
    # another diner's aggregated facts when a separate meal state is available.
    # Existing facts license the complete reference allowlist above, not a
    # blanket exemption for a new statement omitted from this extraction.
    return aliases, foods, attendance, global_foods


def _fully_covered(body: str, values: list[str]) -> bool:
    matched = False
    # A complete separate non-spicy clause is not an unnamed allergen.
    # Its extraction/enforcement stays in the independent non-spicy path.
    rest = re.sub(r"^(?:不辣|不吃辣|不要辣)(?:且|、|和|并且)", "", body)
    for value in sorted(set(values), key=len, reverse=True):
        if not value:
            continue
        pattern = r"(?:某种|某些|一种)?" + re.escape(value)
        rest, count = re.subn(pattern, "", rest)
        matched = matched or bool(count)
    return matched and not _GRAMMAR.sub("", rest).strip(" 、")


def uncovered_allergy_mentions(
    message: str, intent: Intent, state: SessionState
) -> list[UncoveredAllergyMention]:
    """Each positive mention must have literal evidence for its own subject.

    Missing extraction is stored as uncertainty, never inferred as a known food.
    Explicit absent owners remain recorded but do not block active participants.
    This finite guard does not interpret arbitrary pronouns or medical relations.
    """
    aliases, foods, attendance, global_foods = _evidence(intent, state)
    if "过敏" in message and re.search(r"不确定|也许|可能没有|如果.*没有过敏", message):
        # A conditional/uncertain negative cannot clear or confirm safety.
        return [UncoveredAllergyMention(owner=None, raw_clause=message)]
    names = "|".join(re.escape(n) for n in sorted(aliases, key=len, reverse=True))
    owner_pattern = re.compile(
        rf"(?:{names})(?:(?:和|与|及|、)(?:{names}))*(?:的|(?:明确)?说(?:的)?|表示)?"
    )
    gaps = []
    text = _NEGATIVE_ALLERGY.sub("", re.sub(r"\s+", "", message))
    carried_subjects: list[str | None] | None = None
    for clause in _CONNECTORS.split(text):
        clause = clause.strip(" 、")
        carry, carried_subjects = carried_subjects, None
        if "过敏" not in clause:
            bare_owner = owner_pattern.fullmatch(clause)
            if bare_owner:
                carried_subjects = list(aliases.get(bare_owner[0], {None}))
            continue
        if _known_reference(clause, intent, state):
            continue
        start = 0
        for match in re.finditer("过敏", clause):
            prefix = clause[start : match.start()].strip(" 、")
            suffix = clause[match.end() :]
            start = match.end()
            reference = re.match(r"(?:限制|信息|要求|约束|食材)(?:照旧|不变|保留|继续保留)", suffix)
            if reference and _known_reference(prefix + "过敏" + reference[0], intent, state):
                start += reference.end()
                continue
            owners = list(owner_pattern.finditer(prefix))
            body = prefix
            subjects: list[str | None] = [None]
            if owners:
                owner_match = owners[-1]
                body = prefix[owner_match.end() :]
                subjects = list(
                    dict.fromkeys(
                        owner
                        for name in re.split(r"和|与|及|、", owner_match[0])
                        for owner in aliases.get(
                            re.sub(r"(?:的|(?:明确)?说(?:的)?|表示)$", "", name), {None}
                        )
                    )
                )
            elif carry:
                subjects = carry
            elif re.match(r"(?:他|她)(?:的)?对", prefix):
                # A literal earlier named, sole active extracted owner may
                # account for the complete same-food short pronoun statement.
                # No new food/owner is inferred and ambiguous cases stay open.
                factual = [
                    owner
                    for owner, values in foods.items()
                    if values and attendance.get(owner, True)
                ]
                if len(factual) == 1 and any(
                    factual[0] in owners and name in message for name, owners in aliases.items()
                ):
                    candidate = re.sub(r"^(?:他|她)(?:的)?", "", prefix)
                    if _fully_covered(candidate, foods[factual[0]]):
                        subjects, body = [factual[0]], candidate
            if re.match(r"(?:的|原|具体|准确|是|就是|只指|指的是)", suffix):
                # Attribute-before-food and adjective-before-term forms.
                body += "过敏" + suffix
                start = len(clause)
            uncertain = bool(_NON_ASSERTION.search(prefix) or re.match(r"吗|[?？]", suffix))
            for owner in subjects:
                values = global_foods + (foods.get(owner, []) if owner is not None else [])
                if uncertain or not _fully_covered(body, values):
                    gaps.append(
                        UncoveredAllergyMention(
                            owner,
                            clause,
                            attendance.get(owner, True) if owner is not None else True,
                        )
                    )
            if start == len(clause):
                break
    return list(dict.fromkeys(gaps))


def _known_reference(clause: str, intent: Intent, state: SessionState) -> bool:
    """Recognize only complete references to existing facts, never a new assertion.

    This is deliberately a small allowlist, not another natural-language parser.
    In particular, an explanation action alone cannot suppress a missing allergen,
    and a known person's allergies cannot justify an unknown person's reference.
    """
    if not (
        state.constraints.allergies
        or state.pending_allergy
        or state.pending_allergy_terms
        or any(
            d.attendance and (d.pending_allergy or d.pending_allergy_terms) for d in state.diners
        )
    ):
        return False
    known_names = {
        name
        for diner in state.diners
        if diner.attendance
        and (diner.allergies or diner.pending_allergy or diner.pending_allergy_terms)
        for name in [diner.display_name, *diner.aliases]
    }
    subject = ""
    if known_names:
        names = "|".join(re.escape(name) for name in sorted(known_names, key=len, reverse=True))
        subject = rf"(?:(?:{names})(?:的)?)?"
    previous = r"(?:(?:之前|此前|原有|已有|原来|当前|现有)(?:的)?)?"
    facts = rf"{subject}{previous}过敏(?:限制|信息|要求|约束|食材|检查)"
    preserve = rf"(?:{facts}(?:照旧|不变|保留|继续保留)|(?:保留|沿用|保持){facts}(?:不变|照旧)?)"
    if re.fullmatch(preserve, clause):
        return True
    # Named references must name only already-known facts. A missing/unnamed
    # allergen cannot be justified by some other person's known allergy.
    named = re.fullmatch(r"(.+?)过敏(?:也)?(?:仍然|仍)?(?:保持|保留|要遵守|继续保留|照旧|不变)", clause)
    if named and _only_known_foods(named.group(1), state):
        return True
    if any(
        diner.profile_owner
        and diner.attendance
        and (diner.allergies or diner.pending_allergy or diner.pending_allergy_terms)
        for diner in state.diners
    ) and re.fullmatch(
        r"(?:沿用|按|按照|保留)(?:我(?:的)?|用户)?(?:档案|画像)(?:中|里)?(?:的)?"
        r"过敏(?:和健康)?(?:要求|限制|信息)",
        clause,
    ):
        return True
    if intent.action == "explain":
        named_check = re.fullmatch(
            r"(?:以及)?(?:你)?(?:如何|怎么|怎样)(?:检查|避开|遵守)(.+?)过敏", clause
        )
        if named_check and _only_known_foods(named_check.group(1), state):
            return True
        explain = (
            rf"(?:请)?解释(?:一下)?(?:{facts}|(?:这份菜单)?"
            rf"(?:如何|怎么|怎样)(?:避开|满足|遵守){facts})"
        )
        return re.fullmatch(explain, clause) is not None
    return False


def _only_known_foods(text: str, state: SessionState) -> bool:
    parts = re.split(r"和|与|及|、", text)
    return bool(parts) and all(part in state.constraints.allergies for part in parts)


def repair_proven_reference_pending(state: SessionState) -> None:
    """Repair a legacy false flag only with complete short-session evidence.

    An empty terms list alone proves nothing. Unknown origin, truncated history,
    named pending terms, and earlier unknown assertions remain blocked.
    """
    if not state.pending_allergy or state.pending_allergy_terms:
        return
    if not state.constraints.allergies:
        return
    if any(d.pending_allergy or d.pending_allergy_terms for d in state.diners):
        return
    users = [entry.get("content", "") for entry in state.history if entry.get("role") == "user"]
    if not (0 < state.revision <= 6 and len(users) == state.revision and users[-1] == state.last_message):
        return
    if "过敏" not in users[-1]:
        return
    for message in users:
        action = "explain" if "解释" in message else "plan"
        if requires_allergy_clarification(message, Intent(action=action), state):
            return
    state.pending_allergy = False


def requires_allergy_clarification(
    message: str, intent: Intent, state: SessionState
) -> bool:
    """Detect an allergy mention with neither extracted facts nor a known reference.

    Both top-level and attributed facts satisfy the same operation contract.
    References may also point to an already pending fact without resolving it.
    Known/unknown ingredient mapping and persistent pending state remain the
    service's responsibility; this helper never resolves or clears that state.
    """
    return any(mention.active for mention in uncovered_allergy_mentions(message, intent, state))
