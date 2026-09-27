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
    facts = rf"{subject}{previous}过敏(?:限制|信息|要求|约束|食材)"
    preserve = rf"(?:{facts}(?:照旧|不变|保留|继续保留)|(?:保留|沿用|保持){facts}(?:不变|照旧)?)"
    if re.fullmatch(preserve, clause):
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
        explain = (
            rf"(?:请)?解释(?:一下)?(?:{facts}|(?:这份菜单)?"
            rf"(?:如何|怎么|怎样)(?:避开|满足|遵守){facts})"
        )
        return re.fullmatch(explain, clause) is not None
    return False


def requires_allergy_clarification(
    message: str, intent: Intent, state: SessionState
) -> bool:
    """Detect an allergy mention with neither extracted facts nor a known reference.

    Both top-level and attributed facts satisfy the same operation contract.
    References may also point to an already pending fact without resolving it.
    Known/unknown ingredient mapping and persistent pending state remain the
    service's responsibility; this helper never resolves or clears that state.
    """
    if (
        intent.allergies
        or intent.allergy_clarifications
        or any(update.allergies or update.allergy_clarifications for update in intent.diner_updates)
    ):
        return False
    text = _NEGATIVE_ALLERGY.sub("", message)
    return any(
        "过敏" in clause and not _known_reference(clause, intent, state)
        for part in re.split(r"[，,。；;！!？?\n]", text)
        if (clause := part.strip())
    )
