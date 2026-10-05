"""Confirmed revocation of ordinary dietary exclusions.

A revocation request first creates a pending operation and asks for
confirmation; only an explicit confirmation performs a precise removal. This
path never touches allergies, health goals or any hard constraint.
"""

from app.agent.clarification import revoke_asserted_context
from app.domain.models import Intent, PendingRevokeExclusion, SessionState


def _existing_exclusions(state: SessionState, subject: str | None) -> list[str]:
    """Ordinary exclusions for a subject; None means the speaker's meal level."""
    if subject is None:
        meal = state.meal_constraints or state.constraints
        return list(meal.excluded_ingredients)
    for diner in state.diners:
        if subject in (diner.display_name, *diner.aliases):
            return list(diner.excluded_ingredients)
    return []


def _split(targets: list[str], existing: list[str]) -> tuple[list[str], list[str]]:
    present = set(existing)
    return [t for t in targets if t in present], [t for t in targets if t not in present]


def _remove(state: SessionState, subject: str | None, matched: list[str]) -> None:
    if subject is None:
        meal = state.meal_constraints or state.constraints
        meal.excluded_ingredients = [
            term for term in meal.excluded_ingredients if term not in matched
        ]
        return
    for diner in state.diners:
        if subject in (diner.display_name, *diner.aliases):
            diner.excluded_ingredients = [
                term for term in diner.excluded_ingredients if term not in matched
            ]
            break


def _requests(intent: Intent) -> list[tuple[str | None, list[str]]]:
    result: list[tuple[str | None, list[str]]] = []
    if intent.revoke_exclusions:
        result.append((None, list(intent.revoke_exclusions)))
    for update in intent.diner_updates:
        if update.revoke_exclusions:
            result.append((update.diner, list(update.revoke_exclusions)))
    return result


def _clarify(subject: str | None, matched: list[str], unmatched: list[str]) -> str:
    label = f"{subject}的" if subject else ""
    parts: list[str] = []
    if matched:
        parts.append(f"确认取消{label}忌口「{'、'.join(matched)}」？")
    if unmatched:
        parts.append(f"未找到{label}忌口「{'、'.join(unmatched)}」")
    parts.append("其余忌口与过敏会保留；回复「确认」执行，回复「算了」取消。")
    return "".join(parts)


def apply_revoke_exclusion(
    state: SessionState, intent: Intent, message: str
) -> str | None:
    """Process a revoke request/confirm/cancel; returns a clarification or None.

    The first request only records a pending operation; the state is mutated
    solely after an explicit confirmation, and only for ordinary exclusions.
    """
    pending = state.pending_revoke_exclusion
    requests = _requests(intent)

    if pending is not None:
        if intent.revoke_confirmed:
            matched, _ = _split(pending.targets, _existing_exclusions(state, pending.subject))
            _remove(state, pending.subject, matched)
            state.pending_revoke_exclusion = None
            return None
        if intent.revoke_cancelled:
            state.pending_revoke_exclusion = None
            return None
        if requests:
            subject, targets = requests[0]
            matched, unmatched = _split(targets, _existing_exclusions(state, subject))
            state.pending_revoke_exclusion = PendingRevokeExclusion(
                subject=subject, targets=targets
            )
            return _clarify(subject, matched, unmatched)
        return None

    if requests:
        subject, targets = requests[0]
        if not revoke_asserted_context(message):
            return None
        matched, unmatched = _split(targets, _existing_exclusions(state, subject))
        state.pending_revoke_exclusion = PendingRevokeExclusion(
            subject=subject, targets=targets
        )
        return _clarify(subject, matched, unmatched)

    return None
