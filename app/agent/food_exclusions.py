"""Bounded literal food assertions supplement valid authorized extraction."""
import re
from collections.abc import Callable, Sequence

from app.agent.clarification import MEAL_TYPES, asserted_context
from app.domain.dining_scenes import SCENE_TAGS
from app.domain.matching_tags import supported_flavor_preferences

_ASSERTION = re.compile(
    r"(?:请)?(?:(?P<scope>整餐|本餐|这餐|菜单|所有菜|全部菜|我们|大家|我|本人)(?:都)?)?"
    r"(?:不放|不加|不吃|不要放|不要加|别放|别加|不要|避开)"
    r"(?P<foods>[^，,。；;！!\r\n]+?)(?:即可|就好)?"
)
_LOCAL_SCOPE = re.compile(r"第[一二三四五六七八九十\d]+道|这道|那道|这盘|这碗|只(?:换|把|改)|仅(?:换|改)")
_WHOLE_SCOPE = re.compile(r"^(?:请)?(?:整餐|本餐|这餐|菜单|所有菜|全部菜)")
_REFERENCE = re.compile(r"例如|比如|举例|参考|示例|(?:说|表示)[：:,，]")
_STRUCTURAL = frozenset({"汤", "菜", "主食", "荤菜", "素菜", "辣", "蛋白质"})
_PREFERENCE = re.compile(
    r"(?:请)?(?:整餐|本餐|这餐|菜单)?(?:想吃|想要吃|希望吃)"
    r"(?P<food>[^，,。；;！!\r\n]+?)(?:即可|就好)?"
)


def explicit_food_exclusions(
    message: str, supported: Callable[[str], bool], *, whole_meal_only: bool = False,
    self_recorded: Callable[[str], bool] | None = None,
) -> tuple[str, ...]:
    """Recover whole affirmative clauses, not food substrings or unknown names.

    This does not infer a diner's identity, a local slot restriction, removal,
    allergy or cooking authority. The caller must also enforce request scope.
    Unknown food names retain the normal parser/clarification dependency.
    """
    if not asserted_context(message) or _REFERENCE.search(message):
        return ()
    local = whole_meal_only or bool(_LOCAL_SCOPE.search(message))
    result = []
    for clause in re.split(r"[，,。；;！!\r\n]+", message):
        clause = re.sub(r"\s+", "", clause)
        if local and not _WHOLE_SCOPE.match(clause):
            continue
        match = _ASSERTION.fullmatch(clause)
        if match is None:
            continue
        foods = re.split(r"[、和与及]", match["foods"])
        # The complete object must be supported; never turn an unknown food
        # such as 蒜味未收录酱 into a ban on just the 蒜 substring.
        if not foods or any(not food or food in _STRUCTURAL or not supported(food) for food in foods):
            continue
        if match["scope"] in {"我", "本人"} and self_recorded is not None:
            foods = [food for food in foods if not self_recorded(food)]
        result.extend(food for food in foods if food not in result)
    return tuple(result)


def explicit_query_food_preferences(message: str, query_terms: Sequence[str]) -> tuple[str, ...]:
    """Retain an exact unscoped want already extracted as a query-only word.

    A query word alone is not preference authority. Require the complete
    affirmative food object, no local edit, other-diner attribution, quotes,
    questions, examples or conditions. This is not a new food extractor or
    a recipe/safety claim. Unknown requested foods may remain unmet.
    Caller limits this recovery to authorized whole-meal plan/reject actions.
    """
    if not query_terms or not asserted_context(message) or _REFERENCE.search(message) or _LOCAL_SCOPE.search(message):
        return ()
    queries = {re.sub(r"\s+", "", term): term.strip() for term in query_terms if term.strip()}
    result = []
    for clause in re.split(r"[，,。；;！!\r\n]+", message):
        match = _PREFERENCE.fullmatch(re.sub(r"\s+", "", clause))
        if match is None:
            continue
        food = match["food"]
        if (food not in queries or food in _STRUCTURAL | MEAL_TYPES | SCENE_TAGS
                or supported_flavor_preferences((food,))):
            continue
        if queries[food] not in result:
            result.append(queries[food])
    return tuple(result)
