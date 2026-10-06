"""Finite declared-food geometry, independent of title and nutrient quantities."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

ENCLOSURE_VERSION = "declared-meat-grain-enclosure-v2"
EnclosureDirection = Literal["filling_into_meat", "meat_around_filling"]
_WRAPPERS = (
    ("鸡翅中", "鸡中翅", "翅中", "鸡翅"),
    ("鸡腿肉", "鸡腿"),
    ("鸡胸肉", "鸡脯肉", "鸡胸"),
    ("鸭腿肉", "鸭腿"),
    ("猪肉片", "猪里脊片", "里脊片", "猪肉", "猪里脊", "里脊"),
    ("鱿鱼筒", "鱿鱼"),
)
_GRAINS = (
    ("米饭", "白米饭", "大米", "粳米", "籼米", "糯米", "米", "炒饭", "糯米饭"),
    ("小米", "小米饭"),
    ("燕麦", "燕麦饭"),
)
_DECLARATION_PREFIX = r"(?:去骨|无骨|去皮|新鲜|生|熟|薄|厚)*"
_NON_EXECUTED = re.compile(
    r"(?:如果|假如|若|可选|可以|建议|例如|比如|可将|可把)"
    r"|(?:不要|不能|不可|别|勿|不|未|没有|无需|无须)(?:将|把|用)?"
    r"[^，,。；;]{0,12}(?:装入|填入|塞入|包入|放入|封口|扎紧|卷起)"
)
_SEPARATE = re.compile(r"(?:另将|另行|另做|另外|作配菜|作为配菜)")
_CLOSURE = r"(?:用牙签(?:封口|固定)|封口|扎紧|卷起)"
_GRAIN_SUBJECT_START = r"(?:^|(?<=[，,：:将把取盛的好熟]))"


@dataclass(frozen=True)
class MeatEnclosure:
    """Source slice supporting geometry only, not cookedness or portion adequacy.

    Offsets index the original, unmodified preparation. Wrapper/filling foods
    come from the declarations, including their original descriptors. An
    unrecognized food, direction, optional action or separate side is not proof.
    """

    wrapper_food: str
    filling_food: str
    direction: EnclosureDirection
    start: int
    end: int
    text: str


def _declared(foods: list[str], aliases: tuple[str, ...]) -> tuple[str, tuple[str, ...]] | None:
    pattern = _DECLARATION_PREFIX + "(?:" + "|".join(map(re.escape, aliases)) + r")(?:片)?"
    for food in foods:
        if re.fullmatch(pattern, food):
            return food, aliases
    return None


def grain_in_meat_enclosure(ingredient_names: Iterable[str], steps: str) -> MeatEnclosure | None:
    """Recognize a declared meat exterior enclosing a declared grain filling.

    Requires local positive direction plus closure: grain into a meat cavity,
    or meat explicitly wrapping and closing around grain. A container/bowl,
    unbound title, grain exterior or merely mixed ingredients is insufficient.
    This bounded Chinese grammar is not general recipe comprehension, serving
    size, finished-meal eligibility, dietary safety or quantitative nutrition.
    """
    foods = ["".join(food.split()) for food in ingredient_names]
    wrappers = [item for terms in _WRAPPERS if (item := _declared(foods, terms))]
    grains = [item for terms in _GRAINS if (item := _declared(foods, terms))]
    if not wrappers or not grains:
        return None
    # Keep every match bound to its original source offsets despite whitespace.
    positions = [index for index, char in enumerate(steps) if not char.isspace() or char in "\r\n"]
    text = "".join(steps[index] for index in positions)
    for clause_match in re.finditer(r"[^。；;！？!?\r\n]+", text):
        clause = clause_match.group()
        if _NON_EXECUTED.search(clause) or _SEPARATE.search(clause):
            continue
        for wrapper_food, wrapper_aliases in wrappers:
            wrapper = "(?:" + "|".join(map(re.escape, wrapper_aliases)) + ")"
            for filling_food, grain_aliases in grains:
                grain = "(?:" + "|".join(map(re.escape, grain_aliases)) + ")"
                into = (
                    _GRAIN_SUBJECT_START
                    + grain
                    + r"(?:馅|馅料)?(?:装入|填入|塞入|包入|放入)"
                    + wrapper
                    + r"(?:腔内|筒内|内|中)(?=，|,|后|用|封口|扎紧|$)"
                    + r"[^。；;]{0,24}"
                    + _CLOSURE
                )
                around = (
                    wrapper
                    + r"(?:片)?[^。；;，,]{0,12}(?:摊开|平铺|去骨)"
                    + r"[，,]?(?:将|把)?(?:包入|裹入|卷入)"
                    + grain
                    + r"(?:馅|馅料)?(?:后)?[，,]?"
                    + _CLOSURE
                )
                patterns: tuple[tuple[EnclosureDirection, str], ...] = (
                    ("filling_into_meat", into),
                    ("meat_around_filling", around),
                )
                for direction, pattern in patterns:
                    match = re.search(pattern, clause)
                    if match is None:
                        continue
                    start = positions[clause_match.start() + match.start()]
                    end = positions[clause_match.start() + match.end() - 1] + 1
                    return MeatEnclosure(
                        wrapper_food, filling_food, direction, start, end, steps[start:end]
                    )
    return None
