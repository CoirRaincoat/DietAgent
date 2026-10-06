"""Finite explicit dining-scene requests, not inferred portion/safety claims."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

LITERAL_SCENES = frozenset({"家常", "家庭聚餐", "一人食", "宴客", "聚餐", "朋友聚餐", "便当"})
SCENE_TAGS = LITERAL_SCENES | {"圣诞节", "复活节"}
_TERMS = "|".join(re.escape(s) for s in sorted(SCENE_TAGS, key=lambda s: (-len(s), s)))
_SPLIT = re.compile(r"[、,，;；。.!！\r\n]+|不过|但是|可是|然而|而是|但")
_MENTION_ONLY = re.compile(r"如果|假如|例如|比如|解释|为什么|什么叫|是否|[?？\"'“”‘’]")
_REQUEST = re.compile(
    r"^(?:(?:这餐|本餐|这次|今天|明天|我|我们)(?:按|是|要|想要|希望|用于|用来|做)?|"
    r"(?:想要|要|希望|喜欢|偏好|按|用于|用来|做)|(?:用餐)?场景[:：是为]*)?"
    r"(?P<negative>不要|不喜欢|不想要|不用于|不是|避免|别)?"
    r"(?P<scene>" + _TERMS + r")"
    r"(?:风格|用餐|场景|菜|菜肴|菜单)?(?:安排|推荐|即可|就好)?"
    r"(?P<tail>不要|不需要)?$"
)
_UNKNOWN = re.compile(r"^(?:用餐)?场景[:：是为]+(?P<scene>[^:：]{1,30})$")


@dataclass(frozen=True)
class SceneRequests:
    positive: tuple[str, ...]
    negative: tuple[str, ...]
    unknown: tuple[str, ...]


def scene_request_clauses(text: str) -> tuple[str, ...]:
    """Ground only bounded explicit clauses; mentions/questions are not updates.

    Canonical negative text is retained, not silently used to remove a previous
    request. Arbitrary known-scene substrings, portions, workplaces, cuisine,
    clinical goals and inferred populations do not create a scene request.
    """
    if _MENTION_ONLY.search(text):
        return ()
    result: list[str] = []
    for part in _SPLIT.split(text):
        part = re.sub(r"\s+", "", part)
        match = _REQUEST.fullmatch(part)
        if match:
            value = ("不要" if match["negative"] or match["tail"] else "") + match["scene"]
        elif unknown := _UNKNOWN.fullmatch(part):
            value = "场景：" + unknown["scene"]
        else:
            continue
        if value not in result:
            result.append(value)
    return tuple(result)


def scene_requests(preferences: Iterable[str]) -> SceneRequests:
    positive: list[str] = []
    negative: list[str] = []
    unknown: list[str] = []
    for preference in preferences:
        for value in scene_request_clauses(preference):
            if value.startswith("场景："):
                target, tag = unknown, value.removeprefix("场景：")
            elif value.startswith("不要"):
                target, tag = negative, value.removeprefix("不要")
            else:
                target, tag = positive, value
            if tag not in target:
                target.append(tag)
    return SceneRequests(tuple(positive), tuple(negative), tuple(unknown))


def supported_scene_preferences(preferences: Iterable[str], people: int = 1) -> tuple[str, ...]:
    """Keep explicit positive references, withholding conflicting requests."""
    requests = scene_requests(preferences)
    positive = tuple(tag for tag in requests.positive if tag not in requests.negative)
    group = {"家庭聚餐", "朋友聚餐", "聚餐", "宴客"}
    if "一人食" in positive and (people > 1 or group.intersection(positive)):
        positive = tuple(tag for tag in positive if tag != "一人食" and tag not in group)
    return positive


def scene_request_issues(preferences: Iterable[str], people: int = 1) -> tuple[str, ...]:
    requests = scene_requests(preferences)
    issues: list[str] = []
    conflict = [tag for tag in requests.positive if tag in requests.negative]
    if "一人食" in requests.positive and (
        people > 1 or {"家庭聚餐", "朋友聚餐", "聚餐", "宴客"}.intersection(requests.positive)
    ):
        conflict.append("一人食与人数/聚餐要求")
    if conflict:
        issues.append(
            "用餐场景要求存在冲突：" + "、".join(conflict) + "；需要确认，不擅自取舍历史要求。"
        )
    if requests.unknown:
        issues.append("用餐场景尚无可靠映射：" + "、".join(requests.unknown) + "；不能宣称已满足。")
    return tuple(issues)
