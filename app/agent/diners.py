"""Stable participant state and deterministic shared-table aggregation."""

import re
from uuid import NAMESPACE_URL, uuid5

from app.agent.clarification import asserted_context, conditional_context
from app.agent.diet_mode import DIET_LABELS, MODE_ORDER, explicit_diet_mode
from app.domain.matching_tags import explicit_non_spicy_flavor_preference
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


def find_diner(diners: list[Diner], update: DinerUpdate) -> Diner | None:
    """Resolve an attributed update without guessing among shared aliases."""
    reference_keys = {_identity(update.diner), *(_identity(alias) for alias in update.aliases)}
    matches = [diner for diner in diners if _keys(diner) & reference_keys]
    if len(matches) > 1:
        raise DinerConflict(f"无法确定“{update.diner}”对应哪位用餐者，请使用更明确的称呼。")
    return matches[0] if matches else None


def profile_diner(profile: UserProfile) -> Diner:
    """Create the profile owner's participant record without copying raw health data."""
    modes = [explicit_diet_mode(value) for value in profile.preferences]
    explicit_modes = [mode for mode, _ in modes if mode is not None]
    return Diner(
        diner_id=f"profile-{profile.user_id}",
        display_name="用户",
        aliases=["用户", "我", "本人"],
        profile_owner=True,
        participation_basis="profile_unlinked",
        allergies=list(profile.allergies),
        preferences=list(profile.preferences),
        health_goals=list(profile.health_goals),
        no_spicy=explicit_non_spicy_flavor_preference(profile.preferences),
        diet_mode=max(explicit_modes, key=lambda mode: MODE_ORDER[mode]) if explicit_modes else "omnivore",
        pending_diet_mode=any(uncertain for _, uncertain in modes) or len(set(explicit_modes)) > 1,
    )


def is_unlinked_profile(diner: Diner) -> bool:
    """An account holder is not automatically an additional named attendee.

    Old snapshots have no attendance provenance. Keep their profile facts in
    shared checks, but do not invent either a person-to-slot link or absence.
    """
    return diner.profile_owner and diner.participation_basis != "explicit"


def confirmed_attendees(diners: list[Diner]) -> list[Diner]:
    """Return a lower bound of explicitly identified, attending people."""
    return [diner for diner in diners if diner.attendance and not is_unlinked_profile(diner)]


def effective_attendee_count(diners: list[Diner]) -> int:
    return len(confirmed_attendees(diners))


def _owner_attendance(message: str) -> bool | None:
    """Recognize only first-person attendance declarations, not request ownership.

    This bounded confirmation also covers legal intents that attribute only
    relatives' constraints and omit a redundant update for the speaker.
    """
    if not asserted_context(message):
        return None
    decisions: set[bool] = set()
    for clause in re.split(r"[，,。；;\n]", message):
        if re.search(
            r"[?？\"'“”‘’「」]|如果|假如|假设|要是|可能|不确定|是否|他说|她说|有人说"
            r"|听说|转述|讨论|商量|想问",
            clause,
        ):
            continue
        normalized = compact(clause)
        subject = r"(?:^|(?:但|而|还有|以及))(?:(?:今天|今晚|本餐|这次))?(?:我|本人)"
        modifiers = r"(?:(?:今天|今晚|本餐|这次|也|会|要|仍|重新|再次|一起))*"
        absent = re.search(subject + modifiers + r"(?:不|没有|不会|不再)(?:参加|参餐|用餐|吃饭)", normalized)
        present = re.search(subject + modifiers + r"(?:参加|参餐|用餐|吃饭)", normalized)
        shared = re.search(
            r"^(?:今晚|今天|本餐|这次)?(?:我|本人)(?:和|与|跟|及|、).+"
            r"(?:[一二三四五六七八两1-8](?:个)?人|吃饭|用餐|参餐|参加|[早午晚]餐)",
            normalized,
        )
        we_eat = re.search(
            r"^(?:今晚|今天|本餐|这次)?我们.{0,20}(?:人|个).{0,8}(?:吃|餐|参加)", normalized
        )
        plural_negative = re.search(r"(?:不|没|不会|不再)(?:参加|参餐|用餐|吃飯|吃饭)", normalized)
        if absent:
            decisions.add(False)
        if present or ((shared or we_eat) and not plural_negative):
            decisions.add(True)
    if len(decisions) > 1:
        raise DinerConflict("本人是否参加本餐的说法有冲突，请确认；不会猜测本人对应哪位用餐者。")
    return next(iter(decisions)) if decisions else None


def apply_diner_updates(
    diners: list[Diner], updates: list[DinerUpdate], *, session_id: str, message: str = ""
) -> list[Diner]:
    """Apply attributed facts while retaining absent people and stable IDs."""
    result = [diner.model_copy(deep=True) for diner in diners]
    conditional = conditional_context(message)
    for update in updates:
        diner = find_diner(result, update)
        if diner is None:
            diner = Diner(
                diner_id=uuid5(
                    NAMESPACE_URL, f"fangtai-diner:{session_id}:{_identity(update.diner)}"
                ).hex,
                display_name=update.diner.strip(),
                aliases=[],
                attendance=update.attendance is not False,
                participation_basis="explicit",
            )
            result.append(diner)

        if update.attendance is not None:
            # A conditional/hypothetical "won't attend" is not a confirmed exit;
            # keep the prior attendance so the profile's hard constraints survive.
            if not (conditional and update.attendance is False):
                diner.attendance = update.attendance
                diner.participation_basis = "explicit"
        if update.no_spicy is False and diner.no_spicy:
            raise DinerConflict(
                f"{diner.display_name}已有不吃辣的安全限制；如需纠正，请明确说明并走档案核对。"
            )
        if update.no_spicy is True:
            diner.no_spicy = True
        if update.diet_mode is not None:
            diner.diet_mode = update.diet_mode
            diner.pending_diet_mode = False
        diner.aliases = _merge(diner.aliases, [diner.display_name, update.diner, *update.aliases])
        diner.allergies = _merge(diner.allergies, update.allergies)
        diner.excluded_ingredients = _merge(diner.excluded_ingredients, update.excluded_ingredients)
        diner.preferred_ingredients = _merge(
            diner.preferred_ingredients, update.preferred_ingredients
        )
        diner.preferences = _merge(diner.preferences, update.preferences)
        diner.health_goals = _merge(diner.health_goals, update.health_goals)

    owner_attendance = _owner_attendance(message)
    if owner_attendance is not None:
        for diner in result:
            if diner.profile_owner:
                diner.attendance = owner_attendance
                diner.participation_basis = "explicit"

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
    """Aggregate attendees plus unlinked profile facts until explicit absence."""
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
        if MODE_ORDER[diner.diet_mode] > MODE_ORDER[aggregate.diet_mode]:
            aggregate.diet_mode = diner.diet_mode
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
        diet_mode=diner.diet_mode,
    )


def _known_constraints(diner: Diner) -> list[str]:
    known: list[str] = []
    if diner.allergies:
        known.append("过敏：" + "、".join(diner.allergies))
    if diner.excluded_ingredients:
        known.append("不吃：" + "、".join(diner.excluded_ingredients))
    if diner.no_spicy:
        known.append("不吃辣")
    if diner.diet_mode != "omnivore":
        known.append("整餐饮食模式：" + DIET_LABELS[diner.diet_mode])
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
            if not any(rules.preference_matches(recipe, ingredient) for recipe in recipes)
        ]
        result.append(
            DinerSuitability(
                diner_id=diner.diner_id,
                display_name=diner.display_name,
                hard_constraints_satisfied=not violations,
                known_constraints=_known_constraints(diner),
                violations=violations,
                unmet_preferences=unmet,
                scope_note=(
                    "档案主体与本餐用餐者身份待关联；仅保守用于共享限制检查，"
                    "不代表已确认参餐或已关联任一逐人身份。"
                    if is_unlinked_profile(diner)
                    else "仅按该用餐者已知信息核对；未提供的信息保持未知。"
                ),
            )
        )
    return result
