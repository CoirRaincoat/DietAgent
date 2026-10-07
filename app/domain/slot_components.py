"""Finite source evidence for records that cannot represent one ordinary dish.

This does not certify safety, completeness or portion size. Unsplit multi-dish
cards remain original records, rather than fabricated component recipes.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from app.domain.cooking_methods import source_cooking_method_evidence

COMPONENT_VERSION = "source-slot-component-v2-count-decoration"
_CONDIMENT_SUFFIX = ("锅底", "汤底", "烧烤酱", "肉酱", "糖霜")
_PAIRING = re.compile(r".+(?:配|佐|蘸|淋上).+")
_RAW_PROCESS_NAMES = frozenset(
    {"绞肉", "肉末", "猪肉末", "牛肉末", "鸡肉末", "鸡肉泥", "虾泥", "鱼肉泥"}
)
_HEAT = frozenset({"蒸", "煮", "炖", "炒", "烤", "煎", "炸", "焖", "烧"})
_NEGATED_HEAT_CHAIN = re.compile(
    r"(?:无需|不用|不必|不要|不需要|尚未|没有)(?:蒸|煮|炖|炒|烤|煎|炸|焖|烧){1,4}"
)
_COURSE_COUNTS = re.compile(
    r"(?P<dishes>[零〇一二两三四五六七八九十\d]+)(?:道)?菜[、，,＋+]?"
    r"(?P<soups>[零〇一二两三四五六七八九十\d]+)(?:道)?汤$"
)
_SMALL_COUNTS = {word: number for number, word in enumerate("零一二三四五六七八九十")}
_SMALL_COUNTS["两"] = 2
_SMALL_COUNTS["〇"] = 0
_MULTI_PLATE = re.compile(r"[两二三四五六七八九十\d]+盘[^。；;]{0,24}置于")


@dataclass(frozen=True)
class SlotComponentEvidence:
    """Narrow component kind and original-text basis, not a quality score."""

    kind: str
    basis: str


@lru_cache(maxsize=8192)
def slot_component_evidence(name: str, steps: str) -> SlotComponentEvidence | None:
    """Recognize explicit stand-alone components and unsplit course records.

    A condiment attached to a named entree is not banned by its suffix. Bare
    meat-processing names require mechanical processing and no supported main
    heating action. Marketing '套餐' alone supplies no multi-dish evidence:
    separately arranged courses are required, while assembled burgers and a
    single steak plate with sides are retained. Other ambiguous records stay
    unresolved; generic '烹饪结束/即可食用' is not evidence of a dish unit.
    """
    title = "".join(name.split())
    text = "".join(steps.split())
    if title.endswith(_CONDIMENT_SUFFIX) and not _PAIRING.fullmatch(title):
        return SlotComponentEvidence("standalone_condiment", name)
    if (
        title in _RAW_PROCESS_NAMES
        and re.search(r"绞碎|切碎|打碎|搅碎|搅打|打成泥", text)
        and not _HEAT.intersection(
            source_cooking_method_evidence(_NEGATED_HEAT_CHAIN.sub("", steps)).main_methods
        )
    ):
        return SlotComponentEvidence("raw_processing", steps)
    counts = _COURSE_COUNTS.search(title.rstrip(")）】]"))
    if counts:
        values = [
            (
                int(token)
                if token.isdecimal() and len(token) <= 2
                else _SMALL_COUNTS.get(token)
            )
            for token in counts.groups()
        ]
        if all(value is not None for value in values) and sum(value or 0 for value in values) > 1:
            return SlotComponentEvidence("unsplit_multi_course", name)
    if title.endswith("套餐") and "组装" not in text:
        # The reviewed source contrasts have independently placed courses on
        # distinct racks/levels. A main course with '配菜' on the same plate does
        # not satisfy this joint evidence, regardless of its marketing name.
        separate_arrangements = text.count("置于") >= 2
        separate_units = bool(_MULTI_PLATE.search(text)) or bool(
            re.search(r"(?:蒸米饭|米饭|杂粮饭)置于", text)
        )
        separate_supports = "蒸烤盘" in text and "蒸烤架" in text
        if separate_arrangements and separate_units and separate_supports:
            return SlotComponentEvidence("unsplit_multi_course", steps)
    return None
