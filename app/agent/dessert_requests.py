"""Ground explicit dessert allocations; do not guess an extra dinner dish."""

import re

from app.agent.menu_structure import _number

_N = r"[0-9零〇一二两三四五六七八九十]+"
_ROLE = r"(?:饭后)?甜(?:点|品)"
_COUNT = re.compile(rf"(?<![第0-9一二两三四五六七八九十])({_N})(?:道|个|款){_ROLE}")
_ISSUE = "请明确总共几道（包含汤和甜点）、其中几道甜点；不擅自增加菜数或用甜点顶替正餐要求。"


def explicit_dessert_count(message: str, *, total_explicit: bool) -> tuple[int | None, str | None]:
    text = re.sub(r"\s+", "", message)
    if not re.search(_ROLE, text):
        return None, None
    # Quoted examples, questions and hypothetical wording grant no allocation.
    if re.search(r"[“”‘’\"'「」?？]|如果|假如|比如|例如|上次|以前|解释|是否|吗", text):
        return None, None
    clauses = re.split(r"[，,。；;!！]", text)
    no_dessert = any(re.fullmatch(rf"(?:本餐|这餐|饭后)?(?:不要|不需要|不安排|取消){_ROLE}", c)
                     for c in clauses)
    matches = list(_COUNT.finditer(text))
    if not matches:
        if no_dessert:
            return 0, None
        if re.search(rf"(?:要|想吃|安排|搭配|加|来)(?:个|道|款)?{_ROLE}", text):
            return None, _ISSUE
        return None, None
    if (no_dessert or re.search(r"或者|还是|不是|[-~～]", text)
            or re.search(rf"(?:不想|不要|不用|不需要|无需|不安排|取消|不含|不包含|不包括)"
                         rf"(?:安排|吃|加|搭配)?{_N}(?:道|个|款){_ROLE}", text)
            or re.search(rf"{_N}(?:到|至){_N}(?:道|个|款){_ROLE}", text)
            or not total_explicit or "换" in text or not re.search(r"其中|含|包括|包含", text)):
        return None, _ISSUE
    try:
        counts = {_number(m[1]) for m in matches}
    except ValueError:
        return None, _ISSUE
    if len(counts) != 1 or not 0 <= next(iter(counts)) <= 3:
        return None, _ISSUE
    return counts.pop(), None
