"""Stable participant state and deterministic shared-table aggregation."""

from uuid import NAMESPACE_URL, uuid5

from app.domain.models import (
    Constraints,
    Diner,
    DinerSuitability,
    DinerUpdate,
    Recipe,
    UserProfile,
)
from app.rules.engine import RuleEngine, compact


class DinerConflict(Exception):
    """A participant reference cannot be applied without guessing identity."""


def _merge(current: list[str], added: list[str]) -> list[str]:
    return list(dict.fromkeys(current + [item.strip() for item in added if item.strip()]))


def _identity(value: str) -> str:
    aliases = {
        "我爸": "爸爸",
        "父亲": "爸爸",
        "老爸": "爸爸",
        "我妈": "妈妈",
        "母亲": "妈妈",
        "老妈": "妈妈",
        "本人": "用户",
        "我": "用户",
    }
    normalized = compact(value)
    return compact(aliases.get(normalized, normalized))


def _keys(diner: Diner) -> set[str]:
    return {_identity(diner.display_name), *(_identity(alias) for alias in diner.aliases)}


def profile_diner(profile: UserProfile) -> Diner:
    """Create the profile owner's participant record without copying raw health data."""
    return Diner(
        diner_id=f"profile-{profile.user_id}",
        display_name="用户",
        aliases=["用户", "我", "本人"],
        profile_owner=True,
        allergies=list(profile.allergies),
        preferences=list(profile.preferences),
        health_goals=list(profile.health_goals),
    )


def apply_diner_updates(
    diners: list[Diner], updates: list[DinerUpdate], *, session_id: str
) -> list[Diner]:
    """Apply attributed facts while retaining absent people and stable IDs."""
    result = [diner.model_copy(deep=True) for diner in diners]
    for update in updates:
        reference_keys = {_identity(update.diner), *(_identity(alias) for alias in update.aliases)}
        matches = [diner for diner in result if _keys(diner) & reference_keys]
        if len(matches) > 1:
            raise DinerConflict(f"无法确定“{update.diner}”对应哪位用餐者，请使用更明确的称呼。")
        if matches:
            diner = matches[0]
        else:
            diner = Diner(
                diner_id=uuid5(
                    NAMESPACE_URL, f"fangtai-diner:{session_id}:{_identity(update.diner)}"
                ).hex,
                display_name=update.diner.strip(),
                aliases=[],
                attendance=update.attendance is not False,
            )
            result.append(diner)

        if update.attendance is not None:
            diner.attendance = update.attendance
        if update.no_spicy is False and diner.no_spicy:
            raise DinerConflict(
                f"{diner.display_name}已有不吃辣的安全限制；如需纠正，请明确说明并走档案核对。"
            )
        if update.no_spicy is True:
            diner.no_spicy = True
        diner.aliases = _merge(diner.aliases, [diner.display_name, update.diner, *update.aliases])
        diner.allergies = _merge(diner.allergies, update.allergies)
        diner.excluded_ingredients = _merge(diner.excluded_ingredients, update.excluded_ingredients)
        diner.preferred_ingredients = _merge(
            diner.preferred_ingredients, update.preferred_ingredients
        )
        diner.preferences = _merge(diner.preferences, update.preferences)
        diner.health_goals = _merge(diner.health_goals, update.health_goals)

    if len(result) > 8:
        raise DinerConflict("当前单餐最多支持记录 8 位用餐者，请合并或减少成员后再规划。")
    for index, diner in enumerate(result):
        for other in result[index + 1 :]:
            shared = _keys(diner) & _keys(other)
            if shared:
                raise DinerConflict(
                    f"用餐者称呼存在冲突：{diner.display_name}与{other.display_name}共享别名。"
                )
    return result


def aggregate_constraints(meal: Constraints, diners: list[Diner]) -> Constraints:
    """Aggregate all active diners' hard constraints for one shared menu."""
    aggregate = meal.model_copy(deep=True)
    for diner in diners:
        if not diner.attendance:
            continue
        aggregate.allergies = _merge(aggregate.allergies, diner.allergies)
        aggregate.excluded_ingredients = _merge(
            aggregate.excluded_ingredients, diner.excluded_ingredients
        )
        aggregate.preferred_ingredients = _merge(
            aggregate.preferred_ingredients, diner.preferred_ingredients
        )
        aggregate.preferences = _merge(aggregate.preferences, diner.preferences)
        aggregate.health_goals = _merge(aggregate.health_goals, diner.health_goals)
        aggregate.no_spicy = aggregate.no_spicy or diner.no_spicy
    return aggregate


def diner_constraints(diner: Diner) -> Constraints:
    """Build an isolated rule view for one participant's known facts."""
    return Constraints(
        allergies=list(diner.allergies),
        excluded_ingredients=list(diner.excluded_ingredients),
        preferred_ingredients=list(diner.preferred_ingredients),
        preferences=list(diner.preferences),
        health_goals=list(diner.health_goals),
        no_spicy=diner.no_spicy,
    )


def _known_constraints(diner: Diner) -> list[str]:
    known: list[str] = []
    if diner.allergies:
        known.append("过敏：" + "、".join(diner.allergies))
    if diner.excluded_ingredients:
        known.append("不吃：" + "、".join(diner.excluded_ingredients))
    if diner.no_spicy:
        known.append("不吃辣")
    if diner.preferences:
        known.append("口味偏好：" + "、".join(diner.preferences))
    if diner.health_goals:
        known.append("定性目标：" + "、".join(diner.health_goals))
    return known


def diner_suitability(
    recipes: list[Recipe], diners: list[Diner], rules: RuleEngine
) -> list[DinerSuitability]:
    """Evaluate each attendee independently without inventing unknown health facts."""
    result: list[DinerSuitability] = []
    for diner in diners:
        if not diner.attendance:
            continue
        constraints = diner_constraints(diner)
        decisions = [rules.evaluate(recipe, constraints) for recipe in recipes]
        violations = list(
            dict.fromkeys(
                reason
                for decision in decisions
                if not decision.allowed
                for reason in decision.reasons
            )
        )
        unmet = [
            f"未覆盖偏好食材：{ingredient}"
            for ingredient in diner.preferred_ingredients
            if not any(rules.food_matches(recipe, ingredient) for recipe in recipes)
        ]
        result.append(
            DinerSuitability(
                diner_id=diner.diner_id,
                display_name=diner.display_name,
                hard_constraints_satisfied=not violations,
                known_constraints=_known_constraints(diner),
                violations=violations,
                unmet_preferences=unmet,
                scope_note="仅按该用餐者已知信息核对；未提供的信息保持未知。",
            )
        )
    return result
