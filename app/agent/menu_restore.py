"""Scoped menu restore and rejection undo (J18).

A user may reject a whole menu and later change their mind ("恢复第一轮那三道
原菜"). Restoring a menu is not a session rollback and not a blanket clear of
rejection memory: this module records menu version snapshots and per-rejection
deltas, resolves a restore reference deterministically, revalidates the target
menu against the *current* constraints, and — only when it still holds — undoes
exactly the rejection delta that rejected that menu.
"""

from app.domain.models import (
    Intent,
    MenuRevision,
    Recipe,
    RejectionAction,
    SessionState,
)
from app.rules.engine import RuleEngine, compact


def record_menu_revision(
    state: SessionState, recipe_ids: list[str], source: str = "planned_menu"
) -> None:
    """Append a valid menu version; consecutive duplicates are not re-recorded.

    Only a menu actually generated and returned (status=ok) is recorded, so the
    earliest revision stays the stable ``reference="original"`` target.
    """
    last = state.menu_history[-1] if state.menu_history else None
    if last is not None and last.recipe_ids == list(recipe_ids):
        return
    state.menu_history.append(
        MenuRevision(
            revision_id=f"menu-{state.revision}",
            turn_index=state.revision,
            recipe_ids=list(recipe_ids),
            source=source,
        )
    )


def _revision_for(state: SessionState, recipe_ids: list[str]) -> str | None:
    """Return the most recent menu revision with these exact recipe IDs."""
    for revision in reversed(state.menu_history):
        if revision.recipe_ids == list(recipe_ids):
            return revision.revision_id
    return None


def record_rejection_action(state: SessionState, previous_ids: list[str]) -> None:
    """Record only the newly rejected delta and link it to its source revision.

    IDs already rejected before this action are not part of this action's undo
    delta, so undoing it can never resurrect an older, unrelated rejection.
    """
    already = set(state.rejected_recipe_ids)
    newly = [rid for rid in previous_ids if rid not in already]
    if not newly:
        return
    state.rejection_actions.append(
        RejectionAction(
            action_id=f"reject-{state.revision + 1}",
            turn_index=state.revision + 1,
            rejected_recipe_ids=newly,
            source_menu_revision_id=_revision_for(state, previous_ids),
        )
    )


def resolve_menu_revision(state: SessionState, reference: str) -> MenuRevision | None:
    """Resolve a restore reference to a recorded menu revision, or ``None``."""
    if reference == "original":
        return state.menu_history[0] if state.menu_history else None
    return None


def apply_menu_restore(
    state: SessionState,
    intent: Intent,
    rules: RuleEngine,
    recipes: dict[str, Recipe],
) -> str | None:
    """Apply a menu-level restore; returns a clarification when unresolvable.

    All validation happens before any mutation, so a blocked restore leaves the
    menu and rejection memory exactly as they were. The current constraints are
    never dropped to make an old menu fit.
    """
    if intent.restore_menu is None:
        return None
    revision = resolve_menu_revision(state, intent.restore_menu.reference)
    if revision is None:
        return (
            "没有记录到可恢复的历史菜单，无法完成恢复；"
            "请直接说明这一餐的菜品要求。"
        )
    missing = [rid for rid in revision.recipe_ids if rid not in recipes]
    if missing:
        return "历史菜单中的菜品当前已不可用，无法恢复原菜单；请说明新的菜品要求。"
    chosen = [recipes[rid] for rid in revision.recipe_ids]
    if (
        len(chosen) != state.constraints.dish_count
        or sum("soup" in recipe.categories for recipe in chosen) != state.constraints.soup_count
    ):
        return "历史菜单的菜数或汤数与当前要求不一致，无法直接恢复；请调整要求后重试。"
    for recipe in chosen:
        if not rules.evaluate(recipe, state.constraints).allowed:
            return (
                "历史菜单已不满足当前已知约束（过敏/忌口/不辣等），"
                "无法直接恢复；请调整要求后重试。"
            )
    # Undo exactly the rejection delta that rejected this menu revision.
    undo_ids: set[str] = set()
    for action in state.rejection_actions:
        if action.source_menu_revision_id == revision.revision_id and action.active:
            undo_ids.update(action.rejected_recipe_ids)
            action.active = False
    remaining = [rid for rid in state.rejected_recipe_ids if rid not in undo_ids]
    # A still-rejected dish must never re-enter the menu through another ID or
    # a same-name catalog row.
    rejected_names = {
        compact(recipes[rid].name) for rid in remaining if rid in recipes
    }
    for recipe in chosen:
        if compact(recipe.name) in rejected_names:
            return "历史菜单中的菜品仍处于拒绝状态，无法恢复；请调整要求后重试。"
    state.rejected_recipe_ids = remaining
    state.menu_ids = list(revision.recipe_ids)
    return None
