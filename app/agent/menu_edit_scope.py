"""Ground literal batch edits against the CURRENT menu; never guess a lock."""
import re
from collections.abc import Sequence

from app.agent.clarification import asserted_context
from app.domain.models import Intent, Recipe
from app.domain.slot_food_exclusions import local_food_supported
from app.rules.engine import RuleEngine, compact

_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
_NUMBER = r"[0-9一二两三四五六七八九十]+"
_SLOTS = re.compile(rf"第?{_NUMBER}(?:道(?:菜)?)?(?:[、和及与]第?{_NUMBER}(?:道(?:菜)?)?)*道?(?:菜)?")
_OTHER = re.compile(r"(?:另外|其他|其余|剩下)(?:的)?(?P<count>[1-8一二两三四五六七八])?(?:道)?(?:菜)?")
_EXCLUDE = re.compile(r"(?:不要再放|不要放|不要加|不要含|不要有|不想再吃|不想吃|不放|不加|不含|不用|别用|不要|别放|别加|不吃)(.+?)(?:了|就好|即可)?$")
_WHOLE = re.compile(r"^(?:请)?(?:整餐|本餐|这餐|所有菜|全部菜|大家|我|本人)")


def _target(text: str, menu: Sequence[Recipe]) -> list[int] | None:
    text = re.sub(r"(?:不变|别动|不要动|就好|即可)$", "", text)
    if _SLOTS.fullmatch(text):
        tokens = re.findall(_NUMBER, text)
        numbers = [int(n) if n.isdigit() else _NUMBERS.get(n, 0) for n in tokens]
        return numbers if numbers and all(1 <= n <= len(menu) for n in numbers) else []
    hits = [i + 1 for i, recipe in enumerate(menu) if compact(recipe.name) == text]
    return hits if len(hits) == 1 else []


def ground_menu_edit(intent: Intent, message: str, menu: Sequence[Recipe], rules: RuleEngine
                     ) -> tuple[Intent, str | None]:
    """A complete command can override extraction, not original safety facts.

    Unknown names, ambiguous pronouns/counts and locked/edited overlap clarify.
    Explicit whole-meal exclusions remain global, even if a lock conflicts.
    """
    has_fields = bool(intent.replace_slots or intent.keep_slots or intent.local_excluded_ingredients)
    if not asserted_context(message) or re.search(r"例如|比如|举例|报告|样例|示例|解释|说明|引用|文档|日志|原文|模型说", message):
        if has_fields:
            return intent.model_copy(update={"action": "clarify", "clarification": "请直接指定本轮要保留和替换的菜位。",
                "replace_slot": None, "replace_name": None, "replace_slots": [], "keep_slots": [],
                "local_excluded_ingredients": []}), "未从问题或示例授予多菜位修改权限。"
        return intent, None
    clauses = [compact(c) for c in re.split(r"[，,。；;！!\r\n]+", message) if c.strip()]
    kept, edited, foods = [], [], []
    food_scopes: list[tuple[str | None, list[str]]] = []
    only_slots: set[int] | None = None
    others, other_counts = False, []
    saw_batch, issue = False, None
    for clause in clauses:
        keep = re.fullmatch(r"(?:请帮我|请|帮我|我想|我希望)?(?:只|仅|就)?(?:保留|留下|留着)(.+)", clause)
        if keep:
            # Whole-menu preservation is handled by the independent readonly guard.
            if "菜单" in keep[1]:
                continue
            if not (_SLOTS.fullmatch(re.sub(r"(?:不变|别动|就好)$", "", keep[1]))
                    or any(compact(r.name) in keep[1] for r in menu)
                    or re.search(r"这道|那道|另外|其他|其余|剩下", message) or has_fields):
                continue
            slots = _target(keep[1], menu)
            if not slots:
                issue = "保留菜无法唯一对应当前菜单，请明确保留第几道；没有猜测或移动其他菜。"
            else:
                kept.extend(slots)
            saw_batch = True
            continue
        target, rest = None, None
        prefix = re.match(r"(?:请|帮我)?(?:只|仅)?(?:换|替换|更换)(?:掉)?(.+)", clause)
        if prefix:
            target = re.split(r"(?:换成|改成|替换成)", prefix[1], maxsplit=1)[0]
        else:
            suffix = re.match(r"(?:请|帮我)?(?:把)?(.+?)(?:换成|改成|换掉|替换成)(.+)?", clause)
            if suffix:
                target = re.sub(r"(?:给我|帮我)$", "", suffix[1])
        exclusion = _EXCLUDE.search(clause)
        if exclusion:
            rest = re.sub(r"的菜$", "", exclusion[1])
            if rest in {"修改", "调整", "改变", "改", "动", "更换", "换"}:
                # "其他菜不要修改" locks the unmentioned positions; it is
                # not an ingredient exclusion or permission to replace them.
                continue
            head = clause[:exclusion.start()]
            if _OTHER.fullmatch(head) or _SLOTS.fullmatch(head):
                target = head
        if target:
            other = _OTHER.fullmatch(target)
            if other:
                others = True
                if other["count"]:
                    n = other["count"]
                    other_counts.append(int(n) if n.isdigit() else _NUMBERS[n])
            else:
                slots = _target(target, menu)
                if not slots:
                    # Existing single named/number edit consumer handles it.
                    if has_fields or kept or "、" in target or "和第" in target:
                        issue = "替换目标无法对应当前菜单，请明确要换的菜位；不扩大为整餐调整。"
                else:
                    edited.extend(slots)
                    saw_batch |= len(slots) > 1
                    if re.match(r"(?:请|帮我)?(?:只|仅)(?:换|替换|更换)", clause):
                        only_slots = set(slots) if only_slots is None else only_slots & set(slots)
            if rest:
                additions = re.split(r"[、和及与]", rest)
                foods.extend(additions)
                food_scopes.append((target, additions))
        elif exclusion and not _WHOLE.match(clause):
            additions = re.split(r"[、和及与]", re.sub(r"的菜$", "", exclusion[1]))
            foods.extend(additions)
            food_scopes.append((None, additions))
    if others:
        saw_batch = True
        if not kept:
            issue = "“其他菜”缺少明确保留目标，请指定保留第几道、替换哪些菜位。"
        else:
            edited.extend(i for i in range(1, len(menu) + 1) if i not in kept)
            if any(n != len(set(edited)) for n in other_counts):
                issue = "“其他菜”的数量与当前保留菜位不一致，请核对后再换；未改菜单。"
    if not saw_batch and not has_fields:
        return intent, None
    edited, kept = list(dict.fromkeys(edited)), list(dict.fromkeys(kept))
    if only_slots is not None and not set(edited).issubset(only_slots):
        issue = issue or "“只换”的范围与其他换菜/食材范围不一致，请明确；没有扩大修改菜位。"
    if not menu or not edited or set(edited) & set(kept):
        issue = issue or "请明确不同的保留和替换菜位；没有当前菜单或同一道既保留又替换时不执行。"
    # Model-selected positions cannot enlarge or shrink literal authority.
    if (intent.replace_slots and set(intent.replace_slots) != set(edited)
            or intent.keep_slots and set(intent.keep_slots) != set(kept)
            or intent.replace_slot is not None and intent.replace_slot not in edited):
        issue = issue or "解析菜位与原文保留/替换范围不一致，请核对；本轮未执行换菜。"
    whole_foods = [food for food in foods if any(_WHOLE.match(c) and _EXCLUDE.search(c)
                                              and food in c for c in clauses)]
    foods = list(dict.fromkeys(food for food in foods if food and food not in whole_foods))
    if any(not local_food_supported(food, rules) for food in foods):
        issue = issue or "局部不要的食材类别尚不能可靠识别，请明确具体食材；未扩大为整餐禁用。"
    if intent.local_excluded_ingredients and {
        rules.canonical_food(f) for f in intent.local_excluded_ingredients
    } != {rules.canonical_food(f) for f in foods}:
        issue = issue or "解析的局部食材限制与完整原文不一致，请明确哪些菜位不要哪些食材；未遗漏限制后换菜。"
    scopes: dict[int, list[str]] = {}
    for target, additions in food_scopes:
        positions = edited if target is None else (
            [s for s in range(1, len(menu) + 1) if s not in kept] if _OTHER.fullmatch(target)
            else _target(target, menu))
        if not positions or any(s not in edited for s in positions):
            issue = issue or "局部食材限制指向了未授权菜位，请确认保留和替换范围。"
            continue
        for slot in positions:
            scopes[slot] = list(dict.fromkeys([*scopes.get(slot, []), *(f for f in additions if f in foods)]))
    local_keys = {rules.canonical_food(food) for food in foods}
    # Correct only incoming misplaced LOCAL dislikes, never existing global
    # exclusions/allergies or whole-meal assertions and never mutate a profile.
    excluded = [food for food in intent.excluded_ingredients if rules.canonical_food(food) not in local_keys]
    diner_updates = [u.model_copy(update={"excluded_ingredients": [
        food for food in u.excluded_ingredients if rules.canonical_food(food) not in local_keys]})
        for u in intent.diner_updates]
    if issue:
        # Keep replace authority pending, but no guessed confirmed positions.
        return intent.model_copy(update={"action": "replace", "replace_slot": None, "replace_name": None,
            "replace_slots": edited, "keep_slots": kept, "local_excluded_ingredients": [],
            "excluded_ingredients": excluded, "diner_updates": diner_updates, "clarification": None}), issue
    grounded = intent.model_copy(update={"action": "replace", "replace_slot": None, "replace_name": None,
        "replace_slots": edited, "keep_slots": kept, "local_excluded_ingredients": foods,
        "excluded_ingredients": excluded, "diner_updates": diner_updates, "clarification": None})
    grounded._local_food_scopes = scopes
    return grounded, None
