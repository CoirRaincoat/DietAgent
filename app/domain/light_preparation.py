"""Whole-preparation cautions for light-flavor references, not nutrient limits.

Retain original labels/actions. Withhold automatic positive 清淡 references
where explicit frying, oil-finishing or fried-topping evidence needs review.
This never makes a food unsafe, bans ordinary stir-frying or measures salt/oil.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from app.domain.cooking_methods import source_cooking_method_evidence
from app.domain.models import Recipe

LIGHT_PREPARATION_VERSION = "source-light-whole-preparation-v1"
_OIL_ACTION = re.compile(
    r"煸炒|爆炒|爆香|煸香|过油|(?:浇|淋)(?:上|入)?热油|热油(?:淋|浇)" r"|用(?:少许|适量)?油起锅"
)
_CLAUSE = re.compile(r"[^，,。；;\n]+")
_NEGATED = re.compile(
    r"(?:不要|不用|无需|不必|不采用|不需要|禁止|勿|避免|不|未|没有)"
    r"(?:再|进行|使用|加入|添加)?\s*$"
)
_EXAMPLE = re.compile(r"例如|示例|举例|另一个菜|参考做法|有人说")
_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|"[^"]*"|\x27[^\x27]*\x27')
_FRIED_TOPPING = re.compile(r"(?:老)?油条(?:碎|段)?")
_TOPPING_ASSEMBLY = re.compile(r"(?:把|将)?浇头[^，,。；;\n]{0,16}(?:倒|淋|浇)(?:在|到|入)")
_LABEL_SPLIT = re.compile(r"[、,，;；。.!！?？\r\n]+")


@dataclass(frozen=True)
class LightPreparationEvidence:
    """Limited reference review, not actual flavor or medical suitability."""

    cautions: tuple[str, ...]
    source_spans: tuple[tuple[int, int, str], ...]
    method_reference: bool
    policy_version: str = LIGHT_PREPARATION_VERSION

    @property
    def reference_usable(self) -> bool:
        return not self.cautions


@lru_cache(maxsize=8192)
def _observe(names: tuple[str, ...], steps: str, raw_label: str) -> LightPreparationEvidence:
    cautions: list[str] = []
    spans: list[tuple[int, int, str]] = []
    quotes = [(m.start(), m.end()) for m in _QUOTED.finditer(steps)]

    def quoted(position: int) -> bool:
        return any(start <= position < end for start, end in quotes)

    methods = source_cooking_method_evidence(steps)
    topping_assemblies: list[tuple[int, int, str]] = []
    for source_clause in _CLAUSE.finditer(steps):
        if _EXAMPLE.search(source_clause.group()):
            continue
        for assembly in _TOPPING_ASSEMBLY.finditer(source_clause.group()):
            start = source_clause.start() + assembly.start()
            end = source_clause.start() + assembly.end()
            if quoted(start) or _NEGATED.search(source_clause.group()[: assembly.start()]):
                continue
            if re.search(r"不要|不用|无需|不必|不需要|禁止|勿|避免", assembly.group()):
                continue
            topping_assemblies.append((start, end, steps[start:end]))
    for event in methods.events:
        later_topping = next((s for s in topping_assemblies if s[0] > event.end), None)
        review_action = event.method in {"炸", "煎"} or (
            event.method == "炒" and ("炒" not in methods.main_methods or later_topping is not None)
        )
        quoted_executed_mode = bool(re.match(r"[”\"]模式", steps[event.end : event.end + 5]))
        if (
            not review_action
            or event.role
            not in {
                "candidate",
                "preparation",
                "auxiliary",
                "optional",
            }
            or quoted(event.start)
            and not quoted_executed_mode
        ):
            continue
        owner_clause = next(
            (m for m in _CLAUSE.finditer(steps) if m.start() <= event.start < m.end()), None
        )
        if owner_clause is not None and (
            _EXAMPLE.search(owner_clause.group())
            or _NEGATED.search(steps[owner_clause.start() : event.start])
        ):
            continue
        qualifier = "（可选动作）" if event.role == "optional" else ""
        if event.method == "炒" and later_topping is not None:
            qualifier += "（后续浇头组装）"
            spans.append(later_topping)
        cautions.append("原步骤包含" + event.text + qualifier)
        spans.append((event.start, event.end, steps[event.start : event.end]))
    for clause in _CLAUSE.finditer(steps):
        if _EXAMPLE.search(clause.group()):
            continue
        for action in _OIL_ACTION.finditer(clause.group()):
            start, end = clause.start() + action.start(), clause.start() + action.end()
            if quoted(start) or _NEGATED.search(clause.group()[: action.start()]):
                continue
            cautions.append("原步骤包含" + action.group())
            spans.append((start, end, steps[start:end]))
    for name in names:
        if _FRIED_TOPPING.fullmatch(name.strip()):
            cautions.append("原配料声明炸制配料" + name)
    tokens = {token.strip() for token in _LABEL_SPLIT.split(raw_label)}
    if "清淡" in tokens and "油腻" in tokens:
        cautions.append("原标签同时声明清淡与油腻")
    return LightPreparationEvidence(
        tuple(dict.fromkeys(cautions)),
        tuple(dict.fromkeys(spans)),
        bool(set(methods.main_methods) & {"蒸", "煮", "焯"}) and not cautions,
    )


def light_preparation_evidence(recipe: Recipe) -> LightPreparationEvidence:
    """Read complete source steps/declarations without changing recipe metadata.

    Optional frying is kept as a review caution, negated/equipment/example
    mentions are not assertions. Ordinary oil/salt declarations alone do not
    withdraw a reference. Empty cautions do not certify low oil, sodium or
    completeness. Cache keys contain source content, not user/recipe identity.
    """
    return _observe(tuple(i.name for i in recipe.ingredients), recipe.steps, recipe.raw_label)


def light_preparation_warning(recipe: Recipe) -> str | None:
    evidence = light_preparation_evidence(recipe)
    if not evidence.cautions:
        return None
    return (
        f"「{recipe.name}」清淡参考待复核："
        + "、".join(evidence.cautions)
        + "；本轮不以原清淡标签或最后蒸/煮动作加分，仍保留该配方。"
        "未核定实际油盐量、尝味或健康效果。"
    )
