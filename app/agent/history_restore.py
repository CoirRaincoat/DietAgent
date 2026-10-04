"""Field-scoped restore of earlier explicit constraint values (J17).

A user may ask to restore an earlier dish count ("恢复最初的菜数"). This must
restore only that field: allergies, health goals, exclusions, diner identity,
attendance and rejection memory all stay as they are. The model emits a
reference (field + reference), never a guessed value; this module records the
history and resolves the reference deterministically.
"""

from app.domain.models import ConstraintRevision, Intent, SessionState

# Only fields whose explicit values are safe to restore independently.
RESTORABLE_FIELDS = ("dish_count",)


def record_constraint_revision(state: SessionState, field: str, value: int) -> None:
    """Append an explicit user value; consecutive duplicates are not re-recorded.

    The earliest revision is never dropped, so ``reference="original"`` stays
    stable even when the user returns to a previous value.
    """
    last = state.constraint_history[-1] if state.constraint_history else None
    if last is not None and last.field == field and last.value == value:
        return
    state.constraint_history.append(
        ConstraintRevision(field=field, value=value, turn_index=state.revision + 1)
    )


def resolve_constraint_restore(
    history: list[ConstraintRevision], field: str, reference: str
) -> int | None:
    """Resolve a restore reference to a recorded value, or ``None`` when unknown."""
    values = [revision.value for revision in history if revision.field == field]
    if not values:
        return None
    if reference == "original":
        return values[0]
    return None


def apply_constraint_restore(state: SessionState, intent: Intent) -> str | None:
    """Apply field-scoped restores; returns a clarification when unresolvable.

    Only the target field is mutated. Other confirmed constraints and all
    pending state (revoke, rejection memory) are left untouched.
    """
    if not intent.restore_constraints:
        return None
    meal = state.meal_constraints or state.constraints.model_copy(deep=True)
    state.meal_constraints = meal
    for request in intent.restore_constraints:
        resolved = resolve_constraint_restore(
            state.constraint_history, request.field, request.reference
        )
        if resolved is None:
            return (
                "没有记录到可恢复的最初菜数历史，无法完成恢复；"
                "请直接说明这一餐总共要几道菜。"
            )
        if request.field == "dish_count":
            meal.dish_count = resolved
    return None
