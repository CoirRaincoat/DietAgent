"""Finite literal food/slot targets with declared-food and finished-source evidence."""

import re
from collections.abc import Sequence

from app.domain.cooking_methods import main_cooking_methods
from app.domain.entree_preferences import independent_meat_entree_reference
from app.domain.models import CookingMethod, Recipe, ScopedMethod

VERSION = "grounded-food-slot-finished-method-v1"
_METHODS: dict[str, CookingMethod] = {
    "清蒸": "蒸",
    "蒸": "蒸",
    "煮": "煮",
    "炖": "炖",
    "炒": "炒",
    "烘烤": "烤",
    "烤": "烤",
    "煎": "煎",
    "炸": "炸",
    "烧": "烧",
    "红烧": "烧",
    "焖": "焖",
    "凉拌": "拌",
    "拌": "拌",
}
_FOODS = {
    "鸡胸肉": ("鸡胸肉", "鸡胸"),
    "鸡肉": ("鸡肉", "鸡胸肉", "鸡胸", "鸡腿肉", "鸡腿"),
    "牛肉": ("牛肉", "牛腩", "牛排"),
    "猪肉": ("猪肉", "猪瘦肉", "猪里脊"),
    "鱼": ("鱼", "鱼肉", "鱼片", "鲈鱼", "鳕鱼", "三文鱼", "草鱼", "鲳鱼"),
    "虾仁": ("虾仁",),
    "豆腐": ("豆腐", "北豆腐", "南豆腐", "嫩豆腐", "老豆腐", "内酯豆腐"),
    "鸡蛋": ("鸡蛋", "鲜鸡蛋"),
    "南瓜": ("南瓜", "贝贝南瓜"),
    "白菜": ("白菜", "大白菜"),
    "冬瓜": ("冬瓜",),
    "西兰花": ("西兰花",),
    "胡萝卜": ("胡萝卜",),
    "土豆": ("土豆", "马铃薯"),
    "菠菜": ("菠菜",),
    "青菜": ("青菜", "小青菜"),
    "茄子": ("茄子",),
    "香菇": ("香菇", "鲜香菇"),
}
_FOODS.update(
    {
        "鸡腿肉": ("鸡腿肉", "鸡腿"),
        "牛腩": ("牛腩",),
        "牛排": ("牛排",),
        "猪瘦肉": ("猪瘦肉",),
        "猪里脊": ("猪里脊",),
        "鲈鱼": ("鲈鱼",),
        "鳕鱼": ("鳕鱼",),
        "三文鱼": ("三文鱼",),
        "草鱼": ("草鱼",),
        "鲳鱼": ("鲳鱼",),
        "嫩豆腐": ("嫩豆腐",),
        "老豆腐": ("老豆腐",),
        "北豆腐": ("北豆腐",),
        "南豆腐": ("南豆腐",),
        "内酯豆腐": ("内酯豆腐",),
    }
)
_CANONICAL = {alias: key for key, aliases in _FOODS.items() for alias in aliases}
# Keep literal specificity: chicken breast is not broadened to thigh/chicken.
_CANONICAL.update({"鸡胸": "鸡胸肉", "鸡胸肉": "鸡胸肉"})
_FOOD = "|".join(sorted(map(re.escape, _CANONICAL), key=len, reverse=True))
_METHOD = "|".join(sorted(map(re.escape, _METHODS), key=len, reverse=True))
_SPLIT = re.compile(r"[,，;；。.!！\r\n]+")
_MENTION = re.compile(r"如果|假如|比如|例如|解释|是否|或者|[?？\"'“”‘’「」]")
_POSITIVE = re.compile(
    r"^(?P<food>"
    + _FOOD
    + r")(?P<verb>要|想要|希望|喜欢|偏好|尽量)(?P<method>"
    + _METHOD
    + r")(?:的)?$"
)
_METHOD_FIRST = re.compile(
    r"^(?P<verb>要|想要|想吃|希望|喜欢|偏好|尽量)(?P<method>"
    + _METHOD
    + r")(?:的)?(?P<food>"
    + _FOOD
    + r")(?:菜)?$"
)
_CONTRAST = re.compile(
    r"^(?P<food>"
    + _FOOD
    + r")(?:不要|别)(?P<old_method>"
    + _METHOD
    + r")(?:了)?(?:而是|改为|改成|要)(?P<method>"
    + _METHOD
    + r")$"
)
_LOCAL = re.compile(
    r"^(?:只)?(?:把)?第(?P<slot>[1-8一二三四五六七八])道(?:菜)?(?:只)?(?:换成|换为|改成)(?P<method>"
    + _METHOD
    + r")(?:的)?(?P<food>"
    + _FOOD
    + r")$"
)
_PREFIX = r"(?:新鲜|冷冻|冰冻|去皮|去骨|净)*"
_SUFFIX = r"(?:块|片|段|丁|丝|末)*"
_FOOD_PATTERNS = {
    food: re.compile(
        _PREFIX + "(?:" + "|".join(map(re.escape, aliases)) + ")" + _SUFFIX
    )
    for food, aliases in _FOODS.items()
}
_MEAT = frozenset(
    {
        "鸡肉",
        "鸡胸肉",
        "鸡腿肉",
        "牛肉",
        "牛腩",
        "牛排",
        "猪肉",
        "猪瘦肉",
        "猪里脊",
        "鱼",
        "鲈鱼",
        "鳕鱼",
        "三文鱼",
        "草鱼",
        "鲳鱼",
        "虾仁",
    }
)


def explicit_scoped_methods(message: str) -> list[ScopedMethod]:
    """Only finite affirmative raw clauses, not LLM/profile-supplied targets."""
    if _MENTION.search(message) or re.search(r"还是(?:" + _METHOD + ")", message):
        return []
    result: list[ScopedMethod] = []
    for text in _SPLIT.split("".join(message.split())):
        match = _LOCAL.fullmatch(text)
        slot = None
        required = True
        if match:
            numeral = match["slot"]
            slot = (
                int(numeral)
                if numeral.isdigit()
                else "一二三四五六七八".index(numeral) + 1
            )
        else:
            match = _CONTRAST.fullmatch(text)
            if not match:
                match = _POSITIVE.fullmatch(text)
                if not match:
                    match = _METHOD_FIRST.fullmatch(text)
                if match:
                    required = match["verb"] not in {"喜欢", "偏好", "尽量"}
        if match:
            value = ScopedMethod(
                food=_CANONICAL[match["food"]],
                method=_METHODS[match["method"]],
                slot=slot,
                required=required,
            )
            if value not in result:
                result.append(value)
    return result


def scoped_method_mask(
    recipe: Recipe, requests: Sequence[ScopedMethod], *, slot: int | None
) -> int:
    """Match one documented finished method and the named declared source body.

    Names/cache/device modes alone do not prove method, component-level actions
    are not arbitrarily assigned to an ingredient, and meat garnish is not a
    named meat main. This is finite coverage, not portions or all-step exclusion.
    """
    if not requests:
        return 0
    methods = main_cooking_methods(recipe)
    if len(methods) != 1:
        return 0
    mask = 0
    for index, request in enumerate(requests):
        aliases = _FOODS.get(request.food, ())
        pattern = _FOOD_PATTERNS.get(request.food)
        if (
            (request.slot is not None and request.slot != slot)
            or request.method not in methods
            or pattern is None
            or not any(alias in recipe.name for alias in aliases)
            or not any(
                pattern.fullmatch(ingredient.name) for ingredient in recipe.ingredients
            )
            or (request.food in _MEAT and not independent_meat_entree_reference(recipe))
        ):
            continue
        mask |= 1 << index
    return mask


def scoped_method_coverage(
    menu: Sequence[Recipe], requests: Sequence[ScopedMethod]
) -> int:
    result = 0
    for slot, recipe in enumerate(menu, 1):
        result |= scoped_method_mask(recipe, requests, slot=slot)
    return result


def amend_scoped_methods(
    existing: Sequence[ScopedMethod],
    incoming: Sequence[ScopedMethod],
    *,
    menu: Sequence[Recipe] = (),
    local: bool = False,
    replace_slot: int | None = None,
    message: str = "",
) -> list[ScopedMethod]:
    """Bind literal local amendments without withdrawing unrelated requirements.

    The caller supplies only raw-text grounded requests and a confirmed slot.
    An old unbound food-method witness may be superseded only when the edited
    slot actually satisfies it, the new food scope is related, and no untouched
    slot satisfies it. Soft preferences cannot withdraw required methods.
    Contradictory new clauses remain together rather than silently last-wins.
    Outside a local edit, only an explicit raw contrast can withdraw the named
    old method of the exact food scope. Existing slot bindings are retained.
    """
    if local:
        if replace_slot is None or not 1 <= replace_slot <= len(menu):
            return list(existing)
        incoming = [
            r.model_copy(update={"slot": replace_slot})
            for r in incoming
            if r.slot in (None, replace_slot)
        ]
    else:
        incoming = [r for r in incoming if r.slot is None]
    contrasts: list[tuple[str, CookingMethod, CookingMethod]] = []
    if not local and not _MENTION.search(message):
        for clause in _SPLIT.split("".join(message.split())):
            match = _CONTRAST.fullmatch(clause)
            if match:
                food = _CANONICAL[match["food"]]
                old_method = _METHODS[match["old_method"]]
                new_method = _METHODS[match["method"]]
                if (
                    old_method != new_method
                    and ScopedMethod(
                        food=food,
                        method=new_method,
                        required=True,
                    )
                    in incoming
                ):
                    contrasts.append((food, old_method, new_method))
    retained = []
    for old in existing:
        changed = [
            new_method
            for food, old_method, new_method in contrasts
            if food == old.food and old_method == old.method
        ]
        if changed:
            # An explicit food-method cancellation is not permission to drop
            # a previous position. Conflicting new contrasts remain together.
            if old.slot is not None:
                for method in changed:
                    amended = old.model_copy(update={"method": method})
                    if amended not in retained:
                        retained.append(amended)
            continue
        superseded = False
        for new in incoming:
            if not local or (old.required and not new.required):
                continue
            if old.slot == replace_slot:
                superseded = True
                break
            related = (
                old.food == new.food
                or old.food in _FOODS.get(new.food, ())
                or new.food in _FOODS.get(old.food, ())
            )
            if old.slot is not None or not related:
                continue
            assert replace_slot is not None  # Validated local target above.
            if not scoped_method_mask(menu[replace_slot - 1], [old], slot=replace_slot):
                continue
            if any(
                scoped_method_mask(recipe, [old], slot=i)
                for i, recipe in enumerate(menu, 1)
                if i != replace_slot
            ):
                continue
            superseded = True
            break
        if not superseded:
            retained.append(old)
    for request in incoming:
        if request not in retained:
            retained.append(request)
    return retained


def missing_scoped_methods(
    menu: Sequence[Recipe],
    requests: Sequence[ScopedMethod],
    *,
    required_only: bool = False,
) -> list[ScopedMethod]:
    covered = scoped_method_coverage(menu, requests)
    return [
        request
        for index, request in enumerate(requests)
        if not covered & (1 << index) and (request.required or not required_only)
    ]


def scoped_method_label(request: ScopedMethod) -> str:
    return (
        (f"第{request.slot}道：" if request.slot is not None else "")
        + request.food
        + "／"
        + request.method
    )


def scoped_method_warnings(
    menu: Sequence[Recipe], requests: Sequence[ScopedMethod]
) -> list[str]:
    if not requests:
        return []
    missing = missing_scoped_methods(menu, requests)
    met = [request for request in requests if request not in missing]
    values = []
    if met:
        values.append(
            "明确食材／菜位做法的有限来源已覆盖："
            + "、".join(map(scoped_method_label, met))
            + "。"
        )
    if missing:
        values.append(
            "明确食材／菜位做法来源尚缺："
            + "、".join(map(scoped_method_label, missing))
            + "；不把其他食材、其他菜位或口味相似当满足，当前有界同角色调整未补齐。"
        )
    values.append(
        "仅核对声明食材主体与原步骤的有限成菜动作；不是全流程做法排除、份量、用油量或健康功效保证，不改写原配料／步骤。"
    )
    return values
