"""Bounded finished-liquid soup witnesses, not stock/ingredient inference."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from app.domain.cooking_methods import source_cooking_method_evidence

SOUP_EVIDENCE_VERSION = "finished-source-liquid-soup-v3-solid-title"
_STOCKS = frozenset({"高汤", "清汤", "鸡汤", "骨汤", "清水", "水", "矿泉水", "纯净水"})
_IN_SOUP = re.compile(
    r"(?:汤中|汤里|汤内)(?:加入|放入|倒入)[^。；;\n]{0,150}?(?:煮开|煮沸|再次煮开|烧开|煮熟)"
)
_PUREE = re.compile(r"(?:打成|搅打成|搅成)(?:汁|浆|泥)")
_PUREE_BASES = frozenset(
    {"南瓜", "胡萝卜", "西红柿", "番茄", "豌豆", "玉米", "山药", "土豆"}
)
_SOUP_SERVING = re.compile(
    r"(?:连汤(?:盛入|装入|倒入|盛出)|(?:将|把)汤(?:盛入|倒入|装入))(?:汤碗|碗|盅)"
)
_NON_SOUP_FINISH = re.compile(
    r"捞出|沥干|沥水|滤出|过滤取出|倒掉|弃去|收汁|收干|炒熟|煎熟|烤熟|炸熟|蒸熟|凉拌|冷拌|拌匀装盘"
)
_GRAIN_FORMS = ("饭", "面", "粥", "馒头", "包子", "饺", "馄饨", "米粉", "粉丝", "粉条")
_TIMED_SOUP_PROGRAM = re.compile(r"设置[：:]\s*\d+(?:\.\d+)?\s*(?:小时|分钟)\s*[/；;]\s*煲汤")
_QUOTED = re.compile(r"“[^”]*”|「[^」]*」|‘[^’]*’|\"[^\"]*\"|'[^']*'")
_NOT_EXECUTED = re.compile(r"(?:不要|不用|无需|不必|不采用|不能|不再|禁止|勿|不|未|没有)(?:再|进行)?\s*$")
_REFERENCE = re.compile(r"例如|比如|示例|另一个菜|参考|说明|可以|也可|可选")


def _executed_span(text: str, start: int, quotes: list[tuple[int, int]]) -> bool:
    prefix = re.split(r"[，,。；;\n]", text[:start])[-1]
    return not (
        any(left <= start < right for left, right in quotes)
        or _NOT_EXECUTED.search(prefix)
        or _REFERENCE.search(prefix)
    )


def _solid_finish(tail: str) -> bool:
    quotes = [(m.start(), m.end()) for m in _QUOTED.finditer(tail)]
    return any(
        _executed_span(tail, finish.start(), quotes)
        for finish in _NON_SOUP_FINISH.finditer(tail)
    )


@dataclass(frozen=True)
class SoupServingEvidence:
    reason: str
    witness: str


@lru_cache(maxsize=8192)
def solid_soup_title_evidence(
    name: str, ingredient_names: tuple[str, ...], steps: str,
) -> SoupServingEvidence | None:
    """A soup word cannot override executed draining then final solid cooking.

    Require supported final whole-body solid cooking and final serving, plus
    earlier affirmative draining or a stock-prefixed 上汤 dish with declared
    stock. Later soup/stock serving withholds this finite counter-witness.
    This is not a liquid ratio estimate or a generic frying-is-dry claim.
    """
    if not any(term in name for term in ("汤", "羹")):
        return None
    evidence = source_cooking_method_evidence(steps)
    solid_methods = {"炒", "煎", "烤", "炸"}
    stock_title = name.startswith("上汤") and bool(
        set(ingredient_names) & {"高汤", "清汤", "鸡汤"}
    )
    if stock_title:
        solid_methods.add("烧")
    if not set(evidence.main_methods).intersection(solid_methods):
        return None
    events = [
        event for event in evidence.events
        if event.role in {"candidate", "finished_witness"}
    ]
    if not events:
        return None
    last = events[-1]
    quotes = [(m.start(), m.end()) for m in _QUOTED.finditer(steps)]
    if last.method not in solid_methods or not _executed_span(steps, last.start, quotes):
        return None
    tail = steps[last.end :]
    if re.search(r"汤|加(?:入)?(?:清)?水|备用", tail) or not re.search(r"装盘|食用|即可", tail):
        return None
    drains = [
        m for m in re.finditer(r"捞出|沥干|沥水|滤出|倒掉汤|弃去汤", steps[: last.start])
        if _executed_span(steps, m.start(), quotes)
    ]
    if not drains and stock_title and finished_soup_evidence(name, ingredient_names, steps) is None:
        return SoupServingEvidence("stock_prefixed_solid_finish_not_drinking_soup", last.text)
    if not drains:
        return None
    return SoupServingEvidence(
        "drained_body_finished_as_solid_not_drinking_soup",
        steps[drains[-1].start() : last.end],
    )


def finished_soup_evidence(
    name: str,
    ingredient_names: Iterable[str],
    steps: str,
) -> SoupServingEvidence | None:
    """Require soup-object serving/puree or an explicit timed soup program.

    Stock alone, a vessel title, equipment or boiling are insufficient. A
    later draining/reducing or separate solid finish withholds this fallback.
    Existing grain/egg forms are not reinterpreted as an extra soup. This is a
    finite source-text witness, not viscosity, portions or universal role truth.
    Non-meal/component precedence is handled by the caller, never relaxed here.
    """
    title = "".join(name.split())
    if any(term in title for term in (*_GRAIN_FORMS, "蒸蛋", "蛋羹", "蛋饺")):
        return None
    foods = {"".join(food.split()) for food in ingredient_names}
    if not foods.intersection(_STOCKS):
        return None
    # An executed timed 煲汤 command is source soup preparation even if the
    # title ends in an ingredient, not 汤/煲. Appliance names, quoted examples
    # and untimed mode references are insufficient. Later solid finishing
    # takes precedence; this does not infer serving ratios or nutrition.
    quotes = [(m.start(), m.end()) for m in _QUOTED.finditer(steps)]
    programs = [
        m for m in _TIMED_SOUP_PROGRAM.finditer(steps)
        if _executed_span(steps, m.start(), quotes)
    ]
    if programs and not _solid_finish(steps[programs[-1].end():]):
        return SoupServingEvidence(
            "timed_soup_program_without_solid_finish", programs[-1].group()
        )
    text = "".join(steps.split())
    witnesses = list(_IN_SOUP.finditer(text))
    if not witnesses:
        return None
    last = witnesses[-1]
    tail = text[last.end() :]
    if _NON_SOUP_FINISH.search(text[last.start() :]):
        return None
    if not re.search(r"食用|享用|品尝|即可|关火", tail):
        return None
    if _SOUP_SERVING.search(tail):
        return SoupServingEvidence(
            "explicit_soup_serving_after_soup_heating", last.group()
        )
    for puree in _PUREE.finditer(text[: last.start()]):
        # A locally declared food base is required, not incidental garlic paste
        # in an earlier step or a food mentioned in a different sentence.
        prefix = re.split(r"[。；;\n]", text[: puree.start()])[-1][-45:]
        if any(base in prefix for base in foods.intersection(_PUREE_BASES)):
            return SoupServingEvidence(
                "pureed_base_finished_in_soup_then_served", last.group()
            )
    return None


def undrained_pot_reference(
    name: str,
    ingredient_names: Iterable[str],
    steps: str,
) -> SoupServingEvidence | None:
    """Withhold a zero-soup guarantee for a broth-cooked pot with unclear finish.

    A finite title ending 煲, declared water/stock and executed final boiling
    or stewing are jointly required. It does NOT relabel pots as soups, infer
    liquid ratios/portions, or reject every water-cooked food. Explicit final
    draining/reducing/solid finishing removes this uncertainty; preparatory
    blanching/draining before the final broth cooking cannot do so.
    Hotpot is deliberately not screened here: the user means no dedicated
    drinking soup, not a ban on broth used to cook an ordinary hotpot.
    """
    return _undrained_pot(name, tuple(ingredient_names), steps)


@lru_cache(maxsize=8192)
def _undrained_pot(
    name: str, ingredient_names: tuple[str, ...], steps: str
) -> SoupServingEvidence | None:
    title = "".join(name.split())
    if not title.endswith("煲") or any(term in title for term in _GRAIN_FORMS):
        return None
    if not {"".join(food.split()) for food in ingredient_names}.intersection(_STOCKS):
        return None
    evidence = source_cooking_method_evidence(steps)
    if not set(evidence.main_methods).intersection({"煮", "炖"}):
        return None
    events = [
        e
        for e in evidence.events
        if e.method in {"煮", "炖"} and e.role in {"candidate", "finished_witness"}
    ]
    if not events:
        return None
    last = events[-1]
    tail = steps[last.end :]
    quotes = [
        (m.start(), m.end()) for m in re.finditer(r'“[^”]*”|「[^」]*」|"[^"]*"', tail)
    ]
    for finish in _NON_SOUP_FINISH.finditer(tail):
        prefix = re.split(r"[，,。；;\n]", tail[: finish.start()])[-1]
        if (
            not any(start <= finish.start() < end for start, end in quotes)
            and not re.search(
                r"(?:不要|不用|无需|不必|不采用|不|未|没有)(?:再|进行)?\s*$", prefix
            )
            and not re.search(r"例如|比如|示例|另一个菜", prefix)
        ):
            return None
    return SoupServingEvidence(
        "broth_cooked_pot_finish_unverified_not_soup_reclassification", last.text
    )
