"""Keep contradictory count requests separate from verified meal constraints."""

from app.domain.models import Intent, PendingMenuCounts, SessionState


def apply_menu_counts(state: SessionState, intent: Intent) -> str | None:
    """Persist an unresolved pair, or atomically apply a consistent correction.

    A partial correction inherits the other pending number, rather than silently
    reverting to a previous/default value. Unrelated turns cannot clear a conflict.
    Input types and bounds remain the Intent schema's responsibility.
    """
    constraints = state.meal_constraints or state.constraints.model_copy(deep=True)
    state.meal_constraints = constraints
    pending = state.pending_menu_counts
    has_counts = intent.dish_count is not None or intent.soup_count is not None
    if not has_counts and pending is None:
        return None
    base = pending or constraints
    dish_count = intent.dish_count if intent.dish_count is not None else base.dish_count
    soup_count = intent.soup_count if intent.soup_count is not None else base.soup_count
    state.menu_structure_explicit = True
    if soup_count > dish_count:
        state.pending_menu_counts = PendingMenuCounts(dish_count=dish_count, soup_count=soup_count)
        return (
            f"本次要求共 {dish_count} 道菜，其中 {soup_count} 道汤；汤数超过总菜数。"
            "请确认总共几道菜，其中几道汤；此前菜单暂不作为本轮推荐。"
        )
    constraints.dish_count = dish_count
    constraints.soup_count = soup_count
    state.pending_menu_counts = None
    return None
