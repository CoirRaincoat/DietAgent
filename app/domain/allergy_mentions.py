"""Shared conservative guard for omitted allergy information at both entry points."""

import re

from app.domain.models import Intent, SessionState

_NEGATIVE_ALLERGY = re.compile(
    r"(?:没有|没|无)(?:任何|其他|额外)?(?:食物|食材)?过敏(?:史)?|不(?:会)?过敏|非过敏"
)


def _known_reference(clause: str, intent: Intent, state: SessionState) -> bool:
    """Recognize only complete references to existing facts, never a new assertion.

    This is deliberately a small allowlist, not another natural-language parser.
    In particular, an explanation action alone cannot suppress a missing allergen,
    and a known person's allergies cannot justify an unknown person's reference.
    """
    if not (
        state.constraints.allergies or state.pending_allergy or state.pending_allergy_terms
        or any(d.attendance and (d.pending_allergy or d.pending_allergy_terms) for d in state.diners)
    ):
        return False
    known_names = {
        name
        for diner in state.diners
        if diner.attendance and (diner.allergies or diner.pending_allergy or diner.pending_allergy_terms)
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
        diner.profile_owner and diner.attendance
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
    if "过敏" in message and re.search(r"不确定|也许|可能没有|如果.*没有过敏", message):
        return True
    text = _NEGATIVE_ALLERGY.sub("", message)
    clauses = [part.strip() for part in re.split(r"[，,。；;！!？?\n]|并且|但是|且|但", text) if "过敏" in part]
    extracted = list(intent.allergies) + list(intent.allergy_clarifications)
    for update in intent.diner_updates:
        extracted.extend(update.allergies)
        extracted.extend(update.allergy_clarifications)
    for clause in clauses:
        # Explicit unresolved information wins even when other known allergens
        # were extracted in the same turn. The service retains its ownership.
        if re.search(r"(?:还有|另一|另一个|某种|一种).*(?:过敏)|过敏.*(?:忘了|不清楚|没说明|未知)", clause):
            return True
        if _known_reference(clause, intent, state):
            continue
        if any(value in clause for value in extracted):
            continue
        return True
    return False
