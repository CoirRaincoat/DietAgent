"""Source-bound descriptive matching evidence, separate from recipe identities.

Tags here are references for retrieval and soft preference coverage. They do
not certify dining portions, nutrition, allergy safety, non-spiciness, or the
fitness of a preparation component as a finished meal. Source recipes stay
unchanged, including every ingredient and the original labels/steps.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.domain.advance_preparation import is_advance_preparation_permission
from app.domain.dining_scenes import LITERAL_SCENES, SCENE_TAGS, scene_request_clauses
from app.domain.entree_preferences import entree_request_clauses
from app.domain.light_preparation import light_preparation_evidence
from app.domain.meal_context import negative_meal_request_clauses, source_meals
from app.domain.meal_references import meal_reference
from app.domain.method_preferences import method_request_clauses
from app.domain.models import Recipe
from app.domain.scene_references import scene_reference
from app.domain.scoped_methods import explicit_scoped_methods

MATCHING_TAG_VERSION = "source-descriptive-matching-v4-separated-scene-references"
TagOrigin = Literal[
    "source_label", "title_reference", "ingredient_cue", "assistant_review_reference"
]

# Descriptive source flavors only; clinical/population/cuisine prose is absent.
# 葱香/椒盐/糖醋 are literal culinary references, not ingredient substitutions.
_FLAVORS = frozenset(
    {
        "清淡",
        "甜",
        "咸",
        "辣",
        "酸",
        "香",
        "鲜",
        "鲜美",
        "微辣",
        "香辣",
        "咸香",
        "蒜香",
        "咸鲜",
        "香脆",
        "酱香",
        "奶香",
        "豉香",
        "脆",
        "酒香",
        "五香",
        "嫩",
        "黑椒味",
        "原味",
        "软糯",
        "油腻",
        "酸甜",
        "酸辣",
        "葱香",
        "椒盐",
        "糖醋",
    }
)
_ALIASES = {"糖醋": "酸甜"}
# Existing finite vocabulary, exposed for grounded withdrawal parsing only.
FLAVOR_REQUEST_TERMS = _FLAVORS
_TITLE_FLAVORS = ("蒜香", "酸甜", "糖醋", "葱香", "椒盐")
_GARLIC_NAMES = frozenset({"蒜", "大蒜", "蒜瓣", "蒜蓉", "蒜末", "蒜泥", "蒜片", "蒜头"})
_FLAVOR_TERMS = re.compile(
    "|".join(re.escape(term) for term in sorted(_FLAVORS, key=lambda s: (-len(s), s)))
)
_FLAVOR_NEGATION = "|".join(
    re.escape(term) for term in sorted(_FLAVORS, key=lambda s: (-len(s), s))
)
_SPLIT = re.compile(r"[、,，;；。.!！?？\r\n]+")
_CONTRAST = re.compile(r"不过|但是|可是|然而|而是|改为|改成|换成|但")
_NEGATION_HEAD = re.compile(
    r"^(?:不要|不想|不喜欢|不爱|不吃|不能吃|不可以吃|不需要|禁止|"
    r"不(?:太|怎么)(?:喜欢|爱|想)|别|避免|拒绝|去掉|排除|忌|不(?:太|那么|过于)?)"
)
_NEGATED = re.compile(
    r"(?:不要|不想|不喜欢|不爱|不吃|不能吃|不可以吃|不需要|禁止|不(?:太|怎么)(?:喜欢|爱|想)|别|避免|拒绝|去掉|排除|忌"
    r"|不(?:太|那么|过于)?(?=(?:" + _FLAVOR_NEGATION + r")))"
    r".*?(?=但|不过|可是|然而|而是|改为|改成|换成|$)"
)
_TRAILING_NEGATION = re.compile(
    r"(?:也|都)?(?:不要|不喜欢|不想要|不吃|不爱吃|不能吃|不可以吃|不需要|忌|不行)$"
)
_NON_SPICY_HEAD = re.compile(
    r"^(?:(?:现在|这餐|这顿|我|我们|口味|味道)(?:的)?)*"
    r"(?:不能吃辣|不吃辣|不要辣|不辣|辣味不要|辣不要)"
)
_NOT_A_REQUEST = re.compile(r"如果|假如|例如|比如|解释|什么叫|是什么意思|是否|[?？]")
_SCENE_NEGATION = re.compile(r"(?:不是|并非|非|不适合|不用于|不建议用于|不做|不要|不想要)\s*$")
_SINGLE_FLAVOR_BEFORE = re.compile(
    r"^(?:(?:我|我们)?(?:喜欢|想要|想吃|要|偏好|爱吃|希望|口味(?:偏|偏好)?|口感(?:偏)?|味道(?:偏)?|偏)?"
    r"(?:有点|比较|更|稍微|稍|一点)?|.*(?:和|与|或))$"
)
_SINGLE_FLAVOR_AFTER = re.compile(r"^(?:$|味|口|一点|点|一些|些|的|最好|也行|也可以|就好|和|与|或)")
_FLAVOR_FOOD_SUFFIX = re.compile(r"^(?:粉|酱|料|汁|油)")
# Finite compound sensory references, not substring guesses (e.g. 奶香 is
# not inherently sweet and 清淡 is not a verified sodium measurement).
_FLAVOR_COMPONENTS = {
    "酸甜": frozenset({"酸", "甜"}),
    "酸辣": frozenset({"酸", "辣"}),
    "微辣": frozenset({"辣"}),
    "香辣": frozenset({"香", "辣"}),
    "咸香": frozenset({"咸", "香"}),
    "咸鲜": frozenset({"咸", "鲜"}),
    "香脆": frozenset({"香", "脆"}),
}


@dataclass(frozen=True)
class TagEvidence:
    """An explicit reference or weak ingredient cue with its written basis."""

    tag: str
    origin: TagOrigin
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class MatchingTags:
    """Immutable descriptive axes; unknown values are not invented negatives."""

    meals: tuple[str, ...]
    flavors: tuple[TagEvidence, ...]
    scenes: tuple[TagEvidence, ...]
    culinary_role: tuple[str, ...]
    unknown_axes: tuple[str, ...]
    policy_version: str = MATCHING_TAG_VERSION
    meal_references: tuple[TagEvidence, ...] = ()
    scene_references: tuple[TagEvidence, ...] = ()


def _canonical(value: str) -> str:
    return _ALIASES.get(value, value)


def _positive_flavors(clause: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in _FLAVOR_TERMS.finditer(clause):
        if _FLAVOR_FOOD_SUFFIX.match(clause[match.end() :]):
            continue
        if len(match.group()) == 1 and not (
            _SINGLE_FLAVOR_BEFORE.fullmatch(clause[: match.start()].strip())
            and _SINGLE_FLAVOR_AFTER.match(clause[match.end() :].strip())
        ):
            continue
        tag = _canonical(match.group())
        if tag not in found:
            found.append(tag)
    return tuple(found)


def _inline_non_spicy_parts(text: str) -> tuple[str, str] | None:
    """Separate a strict non-spicy atom from an adjacent flavor descriptor.

    不辣清淡 means non-spicy plus a bland flavor reference, not a blanket
    rejection of both. Keep coordinated negation, trailing negation, food
    names and attribution/quotation authority on their existing paths. This
    helper reads source spans; it never rewrites the stored preference.
    """
    text = re.sub(r"\s+", "", text)
    if _TRAILING_NEGATION.search(text):
        return None
    head = _NON_SPICY_HEAD.match(text)
    if head is None:
        return None
    remaining = text[head.end() :]
    if not _FLAVOR_TERMS.match(remaining) or not _positive_flavors(remaining):
        return None
    return text[: head.end()], remaining


def _flavor_requests(
    preferences: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    positive: list[str] = []
    negative: list[str] = []
    unknown: list[str] = []
    for preference in preferences:
        if _NOT_A_REQUEST.search(preference):
            continue
        clauses = [
            subpart
            for part in _SPLIT.sub("；", preference).split("；")
            if not explicit_scoped_methods(part)
            for subpart in _CONTRAST.split(part)
        ]
        for clause in clauses:
            # Contrast clauses and punctuation delimit polarity independently.
            for part in _inline_non_spicy_parts(clause) or clause.split("；"):
                if not part.strip():
                    continue
                text = re.sub(r"\s+", "", part)
                if re.search(
                    r"(?:不是|并非|没有|不代表|并不是)(?:不要|不喜欢|不想|不爱|不吃|不)", text
                ):
                    unknown.append(text)
                    continue
                excluded: list[str] = []
                if _TRAILING_NEGATION.search(text):
                    excluded.extend(_positive_flavors(_TRAILING_NEGATION.sub("", text)))
                    remaining = ""
                else:
                    for span in _NEGATED.finditer(text):
                        excluded.extend(
                            _positive_flavors(_NEGATION_HEAD.sub("", span.group(), count=1))
                        )
                    remaining = _NEGATED.sub("", text)
                included = _positive_flavors(remaining)
                positive.extend(tag for tag in included if tag not in positive)
                negative.extend(tag for tag in excluded if tag not in negative)
                if (
                    not included
                    and not excluded
                    and not scene_request_clauses(text)
                    and not negative_meal_request_clauses(text)
                    and not method_request_clauses(text)
                    and not is_advance_preparation_permission(text)
                    and not entree_request_clauses(text)
                    and text not in {"早餐", "午餐", "晚餐"}
                ):
                    if text not in unknown:
                        unknown.append(text)
    return tuple(positive), tuple(negative), tuple(unknown)


def supported_flavor_preferences(preferences: Iterable[str]) -> tuple[str, ...]:
    """Ordered positive references, excluding any explicit polarity conflict.

    Conversation preferences accumulate rather than replacing the old list.
    A later or simultaneous explicit exclusion therefore cannot leave its old
    positive reference certified. This is bounded textual recognition, not
    sensory negative filtering or a guess at which user statement to discard.
    """
    positive, negative, _unknown = _flavor_requests(preferences)
    return tuple(tag for tag in positive if not any(flavor_overlaps(tag, n) for n in negative))


def flavor_overlaps(reference: str, exclusion: str) -> bool:
    """Directional finite reference entailment; not symmetric taste inference."""
    reference, exclusion = _canonical(reference), _canonical(exclusion)
    return _FLAVOR_COMPONENTS.get(exclusion, frozenset({exclusion})) <= _FLAVOR_COMPONENTS.get(
        reference, frozenset({reference})
    )


def supported_flavor_exclusions(preferences: Iterable[str]) -> tuple[str, ...]:
    return _flavor_requests(preferences)[1]


def flavor_conflicts(preferences: Iterable[str]) -> tuple[str, ...]:
    positive, negative, _ = _flavor_requests(preferences)
    return tuple(tag for tag in positive if any(flavor_overlaps(tag, n) for n in negative))


def negative_flavor_request_clauses(message: str) -> tuple[str, ...]:
    """Supplement omitted unowned negative clauses only on authorized actions.

    No questions, quotations, hypotheticals, removals, ordinal choices or
    attributed diner restrictions become shared constraints via this helper.
    Original polarity-bearing clause is retained instead of manufacturing a
    positive opposite such as 不酸 or promoting a missing source flavor.
    """
    if _NOT_A_REQUEST.search(message) or re.search(r"[‘’“”\"'「」]", message):
        return ()
    found = []
    for part in _SPLIT.split(message):
        text = part.strip()
        if re.search(r"取消|撤销|解除|不再要求|去掉.{1,20}(?:偏好|要求|口味)", text):
            continue  # A separate real negative clause still has authority.
        if re.search(r"妈妈|爸爸|宝宝|同事|朋友|孩子|他|她|仅|只给|不代表", text):
            continue
        if _flavor_requests((text,))[1]:
            found.append(text)
    return tuple(dict.fromkeys(found))


@dataclass(frozen=True)
class FlavorRequestPart:
    index: int
    start: int
    end: int
    text: str
    positive: tuple[str, ...]
    negative: tuple[str, ...]
    pure_flavor_clause: bool


def flavor_request_parts(preference: str) -> tuple[FlavorRequestPart, ...]:
    """Exact spans with conservative pure-flavor removal eligibility.

    Unknown/health/diet/food/method/scene text cannot be retracted via this
    path; it stays verbatim. Delimiters and unaffected spans are not rewritten.
    """
    if _NOT_A_REQUEST.search(preference) or re.search(r"[‘’“”\"'「」]", preference):
        return ()
    term = "(?:" + _FLAVOR_TERMS.pattern + ")"
    pure = re.compile(
        r"^(?:(?:现在|这餐|这顿|我|我们|我的|口味|口感|味道)(?:的)?)*"
        + "(?:"
        + _NEGATION_HEAD.pattern.removeprefix("^")
        + r"|喜欢|爱吃|想吃|想要|要|偏好|偏|希望)?"
        + term
        + r"(?:(?:和|与|及|或)"
        + term
        + r")*"
        + r"(?:口味|味|口|一点|点|一些|些|的|最好|也行|就好)?"
        + "(?:"
        + _TRAILING_NEGATION.pattern.removesuffix("$")
        + ")?$"
    )
    parts: list[FlavorRequestPart] = []
    start = 0
    ends = [
        (m.start(), m.end())
        for m in re.finditer(_SPLIT.pattern + "|" + _CONTRAST.pattern, preference)
    ]
    ends.append((len(preference), len(preference)))
    for end, next_start in ends:
        text = preference[start:end]
        if text.strip():
            positive, negative, unknown = _flavor_requests((text,))
            parts.append(
                FlavorRequestPart(
                    len(parts),
                    start,
                    end,
                    text.strip(),
                    positive,
                    negative,
                    bool(
                        (positive or negative)
                        and not unknown
                        and pure.fullmatch(re.sub(r"\s+", "", text))
                    ),
                )
            )
        start = next_start
    return tuple(parts)


def flavor_exclusion_hits(recipe: Recipe, preferences: Iterable[str]) -> tuple[str, ...]:
    """Explicit raw label/title reference hits; ingredient cues are not flavors."""
    exclusions = supported_flavor_exclusions(preferences)
    return tuple(
        "口味排除「"
        + excluded
        + "」命中来源参考「"
        + item.tag
        + "」："
        + "；".join(item.evidence)
        + "。"
        for item in matching_tags(recipe).flavors
        if item.origin != "ingredient_cue"
        for excluded in exclusions
        if flavor_overlaps(item.tag, excluded)
    )


def explicit_non_spicy_flavor_preference(preferences: Iterable[str]) -> bool:
    """Only strict finite non-spicy requests; 不太辣 is not zero-spice evidence."""
    return any(
        bool(
            re.fullmatch(
                r"(?:现在|这餐|这顿|我|我们|口味)?(?:不辣|不要辣|不吃辣|不能吃辣|辣味不要|辣不要)",
                re.sub(r"\s+", "", text),
            )
        )
        or _inline_non_spicy_parts(text) is not None
        for text in negative_flavor_request_clauses("；".join(preferences))
    )


def negative_flavor_menu_disclosure(
    menu: Iterable[Recipe], preferences: Iterable[str]
) -> tuple[str, ...]:
    """Report source exclusion and unknown taste per selected dish, not success."""
    exclusions = supported_flavor_exclusions(preferences)
    if not exclusions:
        return ()
    statements = [
        "已按明确来源参考排除口味："
        + "、".join(exclusions)
        + "；尚未实现完整味觉排除，不从未标注或其他口味标签推断不酸、不甜、低盐等实际口味。"
    ]
    for recipe in menu:
        hits = flavor_exclusion_hits(recipe, preferences)
        if hits:
            statements.extend(hits)
        else:
            statements.append(
                "「" + recipe.name + "」未见命中上述排除项的明确口味参考，实际味觉无法核验。"
            )
    return tuple(statements)


def flavor_preference_issues(
    preferences: Iterable[str], *, excluded_ingredients: Iterable[str] = (),
) -> tuple[str, ...]:
    """Disclose unsupported/negative/conflicting clauses, never silently pass.

    Negative sensory preferences need separate implementation/clarification;
    the independent non-spicy and allergen hard gates remain in force. No
    warning is a waiver, a tasted result, or a claim of matching these clauses.
    """
    values = tuple(preferences)
    positive, negative, unknown = _flavor_requests(values)
    # Do not reclassify a complete, already structured ingredient prohibition
    # as an unknown taste. This only changes disclosure: it grants no exclusion
    # authority, flavor coverage or safety exemption; aliases/partial matches
    # and unrecorded prohibitions remain unsupported here.
    excluded = {re.sub(r"\s+", "", value) for value in excluded_ingredients}
    unknown = tuple(text for text in unknown if not (
        (match := re.fullmatch(r"(?:不放|不加|不要放|不要加)(.+)", text))
        and match[1] in excluded
    ))
    issues = []
    conflict = flavor_conflicts(values)
    if conflict:
        issues.append(
            "已记录的正负口味偏好存在冲突："
            + "、".join(conflict)
            + "；需要确认，不能宣称该口味已满足。"
        )
    if negative:
        descriptions = [
            value for value in values if _NEGATED.search(value) or _TRAILING_NEGATION.search(value)
        ]
        issues.append(
            "负向口味偏好只排除明确来源参考，尚未实现完整味觉排除："
            + "、".join(descriptions)
            + "；不辣与过敏仍按独立硬限制检查，不以口味标签放行。"
        )
    if unknown:
        issues.append("以下偏好暂无可靠口味映射，尚未核验：" + "、".join(unknown) + "。")
    return tuple(issues)


def matching_tags(recipe: Recipe) -> MatchingTags:
    """Read exact source labels and bounded title references without mutation.

    A cached meal tag is used only under the established empty-raw-label
    compatibility contract. Flavor/scene caches are never promoted as source
    evidence. Garlic presence is exported only as a cue, not 蒜香 coverage.
    """
    raw_tokens = tuple(
        dict.fromkeys(token.strip() for token in _SPLIT.split(recipe.raw_label) if token.strip())
    )
    flavors: dict[str, TagEvidence] = {}
    for token in raw_tokens:
        if token in _FLAVORS:
            tag = _canonical(token)
            flavors.setdefault(tag, TagEvidence(tag, "source_label", ("原始标签：" + token,)))
    name = recipe.name.strip()
    for prefix in _TITLE_FLAVORS:
        if name.startswith(prefix):
            tag = _canonical(prefix)
            flavors.setdefault(
                tag, TagEvidence(tag, "title_reference", ("原始菜名：" + recipe.name,))
            )
    garlic = tuple(dict.fromkeys(i.name for i in recipe.ingredients if i.name in _GARLIC_NAMES))
    if garlic and "蒜香" not in flavors:
        flavors["蒜香"] = TagEvidence(
            "蒜香", "ingredient_cue", tuple("声明含蒜：" + name for name in garlic)
        )

    scenes: dict[str, TagEvidence] = {}
    for token in raw_tokens:
        if token in SCENE_TAGS:
            scenes.setdefault(token, TagEvidence(token, "source_label", ("原始标签：" + token,)))
    # Literal longest scene references avoid also inferring 聚餐 from 家庭聚餐.
    occupied: list[tuple[int, int]] = []
    for scene in sorted(LITERAL_SCENES, key=lambda value: (-len(value), value)):
        for match in re.finditer(re.escape(scene), name):
            if any(start < match.end() and match.start() < end for start, end in occupied):
                continue
            occupied.append((match.start(), match.end()))
            if _SCENE_NEGATION.search(name[: match.start()]) or (
                scene == "便当" and name[match.end() :].startswith("盒")
            ):
                continue
            scenes.setdefault(
                scene,
                TagEvidence(scene, "title_reference", ("原始菜名：" + recipe.name,)),
            )

    meal_tags = source_meals(recipe)
    meals = tuple(meal for meal in ("早餐", "午餐", "晚餐", "下午茶", "夜宵") if meal in meal_tags)
    roles = tuple(dict.fromkeys(recipe.categories))
    unknown = []
    if not meals:
        unknown.append("meal")
    if not any(evidence.origin != "ingredient_cue" for evidence in flavors.values()):
        unknown.append("flavor")
    if not scenes:
        unknown.append("scene")
    if not roles:
        unknown.append("culinary_role")
    reference = meal_reference(recipe) if not meals else None
    meal_refs = (
        tuple(
            TagEvidence(
                tag,
                "assistant_review_reference",
                (
                    reference.ingredient_excerpt,
                    reference.preparation_excerpt,
                    reference.rationale,
                ),
            )
            for tag in reference.meals
        )
        if reference
        else ()
    )
    scene_ref = scene_reference(recipe)
    scene_refs = (
        tuple(
            TagEvidence(
                tag,
                "assistant_review_reference",
                (
                    scene_ref.ingredient_excerpt,
                    scene_ref.preparation_excerpt,
                    scene_ref.rationale,
                ),
            )
            for tag in scene_ref.scenes
            if tag not in scenes
        )
        if scene_ref
        else ()
    )
    return MatchingTags(
        meals,
        tuple(flavors.values()),
        tuple(scenes.values()),
        roles,
        tuple(unknown),
        meal_references=meal_refs,
        scene_references=scene_refs,
    )


def flavor_strength(recipe: Recipe, preference: str) -> int:
    """Ordinal source-label=3, literal-title=2, cue/unknown=0; no quality score.

    Accept one canonical flavor or one positive single-flavor phrase. Multi-
    flavor phrases use flavor_coverage, rather than claiming their whole match
    from just one of their flavors. A 清淡 label remains a source reference,
    never a low-salt, low-fat or non-spicy certification.
    """
    requested = supported_flavor_preferences((preference,))
    if len(requested) != 1:
        return 0
    if requested[0] == "清淡" and not light_preparation_evidence(recipe).reference_usable:
        return 0
    return max(
        (
            (3 if item.origin == "source_label" else 2 if item.origin == "title_reference" else 0)
            for item in matching_tags(recipe).flavors
            if item.tag == requested[0]
        ),
        default=0,
    )


def flavor_coverage(recipe: Recipe, preferences: Iterable[str]) -> int:
    """Bitmask in supported positive preference order; ingredient cues are zero."""
    known = {item.tag for item in matching_tags(recipe).flavors if item.origin != "ingredient_cue"}
    if "清淡" in known and not light_preparation_evidence(recipe).reference_usable:
        known.discard("清淡")
    return sum(
        1 << index
        for index, preference in enumerate(supported_flavor_preferences(preferences))
        if preference in known
    )


def scene_reference_mask(recipe: Recipe, requested: Iterable[str]) -> int:
    """Source/title or separately authored reference bits, not certification."""
    evidence = matching_tags(recipe)
    tags = {item.tag for item in (*evidence.scenes, *evidence.scene_references)}
    return sum(1 << index for index, tag in enumerate(dict.fromkeys(requested)) if tag in tags)
