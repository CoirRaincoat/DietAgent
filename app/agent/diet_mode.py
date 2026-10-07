"""Finite original-text grounding for whole-table and attributed diet requests."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.models import DietMode, Diner, DinerUpdate, Intent

DIET_LABELS = {"omnivore": "未额外限定素食", "ovo_lacto_vegetarian": "蛋奶素", "vegan": "纯素"}
MODE_ORDER = {"omnivore": 0, "ovo_lacto_vegetarian": 1, "vegan": 2}
DIET_QUESTION = (
    "请明确是整餐蛋奶素（允许蛋奶、不含肉源），还是纯素（也排除蛋奶蜂蜜）；汤、主食和调料均核对。"
)


def explicit_diet_mode(text: str) -> tuple[DietMode | None, bool]:
    """Return grounded mode or unresolved diet mention; no count-to-mode guess.

    Quotes, history, examples and questions cannot mutate a diet. Merely
    excluding eggs/milk or requesting zero meat entrées is not whole-meal vegan.
    """
    text = re.sub(r"\s+", "", text)
    if any(c in text for c in "“”\"'「」") or re.search(
        r"解释|上次|以前|比如|例如|如果|假如|[?？]|是什么意思", text
    ):
        return None, False
    if re.search(r"吗|是否|能否|可不可以", text):
        return None, False
    if re.search(r"取消(?:本餐|整餐)?素食限制|改成杂食|改为杂食", text):
        return "omnivore", False
    text = re.sub(
        r"(?:不是|不要求|不采用|不要|不吃|不做|不安排)(?:纯素|全素|严格素食|蛋奶素)", "", text
    )
    text = re.sub(r"(?:不|禁止)(?:允许|接受|可以吃)蛋奶", "", text)
    vegan = bool(re.search(r"纯素|全素|严格素食|只吃植物|不含任何动物来源", text))
    lacto = bool(re.search(r"蛋奶素|允许蛋奶|可以吃蛋奶|接受蛋奶", text))
    if vegan and lacto:
        return None, True
    if vegan:
        return "vegan", False
    if lacto:
        return "ovo_lacto_vegetarian", False
    if re.search(
        r"(?:全餐|整餐|这餐|本餐)(?:不吃|不含|不要)(?:任何)?肉(?:类|和肉汤|及肉汤|$)", text
    ):
        return "ovo_lacto_vegetarian", False
    return None, bool(re.search(r"吃素|素食|全素菜|全部素菜", text))


@dataclass(frozen=True)
class DietGrounding:
    """Grounded updates and owner-scoped uncertainty; unrelated facts survive."""

    intent: Intent
    meal_mode: DietMode | None
    owner_modes: dict[str, DietMode]
    pending_owners: set[str]
    pending_meal: bool = False


def ground_diet_intent(message: str, intent: Intent, diners: Sequence[Diner]) -> DietGrounding:
    """Anchor each complete clause to a literal owner or explicit table request.

    Missing model fields are filled from finite text. Ungrounded model fields
    are dropped and clarified; pronouns or mixed-owner clauses are not guessed.
    Conflicting modes within one turn are not silently ordered by strength.
    """
    if (
        intent.action == "explain"
        or re.search(r"如果|假如|[?？]", message)
        or any(c in message for c in "“”\"'「」")
    ):
        return DietGrounding(
            intent.model_copy(
                update={
                    "diet_mode": None,
                    "diner_updates": [
                        u.model_copy(update={"diet_mode": None}) for u in intent.diner_updates
                    ],
                }
            ),
            None,
            {},
            set(),
        )
    aliases = {
        "我妈": "妈妈",
        "妈妈": "妈妈",
        "我爸": "爸爸",
        "爸爸": "爸爸",
        "孩子": "孩子",
        "用户": "用户",
        "本人": "用户",
        "我": "用户",
    }
    for diner in diners:
        for alias in [diner.display_name, *diner.aliases]:
            aliases[alias] = diner.display_name
    for update in intent.diner_updates:
        for alias in [update.diner, *update.aliases]:
            if alias not in {"他", "她", "他们", "她们"}:
                aliases[alias] = update.diner
    owner_modes: dict[str, DietMode] = {}
    pending_owners: set[str] = set()
    meal_mode: DietMode | None = None
    pending_meal = False
    for clause in re.split(r"[，,。；;！!\n]", message):
        clause = re.sub(r"^\s*(?:但是|另外|同时|不过|并且|但|而)", "", clause)
        mode, unresolved = explicit_diet_mode(clause)
        if mode is None and not unresolved:
            continue
        if re.match(r"\s*(?:给|为)?(?:我们|我和|我与|爸妈|他们|她们)", clause):
            pending_meal = True
            continue
        owner: str | None = None
        bad_reference = False
        if not re.match(r"(?:全餐|整餐|整桌|所有人)", clause.strip()):
            for alias in sorted(aliases, key=len, reverse=True):
                if match := re.match(rf"\s*(?:只给|仅给|给|为)?{re.escape(alias)}", clause):
                    if re.match(r"们|(?:的)?(?:朋友|同事|同学)", clause[match.end() :]):
                        bad_reference = True
                        break
                    owner = aliases[alias]
                    break
        if bad_reference:
            pending_meal = True
            continue
        if owner is None and re.search(r"她|他|某人|有人|朋友", clause):
            pending_meal = True
            continue
        if owner:
            # Multiple people in one incomplete clause need explicit attribution.
            if re.search(r"(?:我|妈妈|爸爸)(?:和|与|、)|爸妈|他们|她们", clause):
                pending_meal = True
                continue
            if unresolved or (owner in owner_modes and owner_modes[owner] != mode):
                pending_owners.add(owner)
            elif mode is not None:
                owner_modes[owner] = mode
        elif unresolved or (meal_mode is not None and meal_mode != mode):
            pending_meal = True
        else:
            meal_mode = mode
    for owner in pending_owners:
        owner_modes.pop(owner, None)
    updates: list[DinerUpdate] = []
    covered: set[str] = set()
    for update in intent.diner_updates:
        mode = owner_modes.get(update.diner)
        if update.diet_mode is not None and mode is None:
            pending_owners.add(update.diner)
        updates.append(update.model_copy(update={"diet_mode": mode}))
        covered.add(update.diner)
    for owner in (set(owner_modes) | pending_owners) - covered:
        updates.append(DinerUpdate(diner=owner, diet_mode=owner_modes.get(owner)))
    if intent.diet_mode is not None and meal_mode is None:
        pending_meal = True
    if pending_meal:
        meal_mode = None
    updated = intent.model_copy(update={"diet_mode": meal_mode, "diner_updates": updates})
    return DietGrounding(updated, meal_mode, owner_modes, pending_owners, pending_meal)
