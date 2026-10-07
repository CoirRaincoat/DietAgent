"""Check a small set of explicit menu-count expressions against model output."""

import re

_DIGITS = "零〇一二两三四五六七八九"
_NUMBER = r"[0-9零〇一二两三四五六七八九十]+"
_BOUNDARY = rf"(?<![第0-9{_DIGITS}十百千万])"
_SOUP_NAME = r"(?:荤菜|素菜|蔬菜|荤|素|肉|清淡|清)?汤(?!面|料|粉|汁|包)"
# Bare 道 is an absolute total only at a clause/count boundary. A following
# role or food noun (独立荤菜/豆腐/主食...) belongs to a component quantity;
# recognizing unknown food names is not required to exclude that scope.
_BARE_TOTAL_END = (
    r"(?=$|[，,。；;:：、!！?？]|总数|总计|总共|共计|合计|其中|包括|包含|含"
    r"|不要|不用|不需要|无需|无须|无汤|不安排|就好|就行|就够|够吃|够了|即可|足够|都要|分别)"
)
_SEPARATE = re.compile(
    rf"{_BOUNDARY}(?P<dishes>{_NUMBER})(?:道菜|道|菜)"
    rf"(?P<link>[，,、+和加]|另加)?(?P<soups>{_NUMBER})(?:道|个|款)?{_SOUP_NAME}"
)
_TOTAL = re.compile(
    rf"{_BOUNDARY}(?P<count>{_NUMBER})"
    rf"(?:道菜(?!汤|心|花|籽|苗|包)|道{_BARE_TOTAL_END}|个菜|款菜)"
)
_SOUP = re.compile(
    rf"{_BOUNDARY}(?P<count>{_NUMBER})(?:道|个|款)?"
    r"(?P<kind>荤菜|素菜|蔬菜|荤|素|肉|清淡|清)?汤(?!面|料|粉|汁|包)"
)
_SOURCE_SOUP_NAME = r"(?:荤菜|素菜|荤|素|肉)汤(?!面|料|粉|汁|包)"
_SOURCE_FIELDS = {
    "荤": "meat_soup_count",
    "荤菜": "meat_soup_count",
    "肉": "meat_soup_count",
    "素": "vegetarian_soup_count",
    "素菜": "vegetarian_soup_count",
}
_ISSUE = "请明确本餐总共几道菜、其中几道汤；总菜数包含汤。"


def _negated_count_clause(text: str) -> bool:
    """A food/health negation is not evidence that menu numbers are denied.

    Keep actual count clauses and a standalone denial conservative. This is
    only the scope of literal 不是; conditional, alternative, range and quoted
    requests retain their existing guards, not a general intent parser.
    """
    return any(
        "不是" in clause
        and (
            clause == "不是"
            or _TOTAL.search(clause)
            or _SOUP.search(clause)
            or _SEPARATE.search(clause)
            or re.search(r"(?:不要|不用|不需要|无需|不安排|无)汤", clause)
        )
        for clause in re.split(r"[，,。；;:：!！?？]", text)
    )


def _has_total_prefix(text: str, start: int) -> bool:
    return re.search(r"(?:总共|总计|合计|共计|一共|共)$", text[:start]) is not None


def _number(text: str) -> int:
    if text.isascii() and text.isdigit():
        return int(text)
    digits = {char: index for index, char in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    if text in digits:
        return digits[text]
    if text.count("十") == 1:
        tens, units = text.split("十")
        if (not tens or tens in digits) and (not units or units in digits):
            return (digits[tens] if tens else 1) * 10 + (digits[units] if units else 0)
    raise ValueError("Unsupported number expression")


def _soup_count_evidence(text: str) -> tuple[dict[str, int], set[int], str | None]:
    """Keep source subgroup quantities separate from a declared total."""
    matches = list(_SOUP.finditer(text))
    source_counts: dict[str, int] = {}
    try:
        for field in set(_SOURCE_FIELDS.values()):
            values = {
                _number(m["count"]) for m in matches if _SOURCE_FIELDS.get(m["kind"]) == field
            }
            if len(values) > 1 or any(not 0 <= n <= 3 for n in values):
                return {}, set(), _ISSUE
            if values:
                source_counts[field] = values.pop()
        totals = {_number(m["count"]) for m in matches if m["kind"] not in _SOURCE_FIELDS}
        if re.search(r"(?:不要|不用|不需要|无需|不安排|无)汤", text):
            totals.add(0)
    except ValueError:
        return {}, set(), _ISSUE
    if len(totals) > 1 or (totals and sum(source_counts.values()) > next(iter(totals))):
        return {}, set(), _ISSUE
    qualified = [m for m in matches if m["kind"] in _SOURCE_FIELDS]
    generic = [m for m in matches if m["kind"] not in _SOURCE_FIELDS]
    if qualified and any(
        m.start() > qualified[0].start() and not _has_total_prefix(text, m.start()) for m in generic
    ):
        # A later bare count can be an inconsistent restatement, not a proven
        # parent total. Earlier totals or explicit 共/合计 establish that scope.
        return {}, set(), _ISSUE
    if not totals and source_counts:
        totals.add(sum(source_counts.values()))
    if any(not 0 <= n <= 3 for n in totals):
        return {}, set(), _ISSUE
    return source_counts, totals, None


def explicit_soup_composition(message: str) -> tuple[dict[str, int], str | None]:
    """Ground exact declared meat/no-meat soup counts, not light flavor.

    This uses the existing egg/dairy-permitted culinary source convention,
    never a whole-meal diet, ingredient quantity or health claim. Bare names
    without an exact finite quantity remain ungrounded by this contract.
    """
    text = re.sub(r"\s+", "", message)
    if any(c in text for c in "“”‘’\"'「」") or re.search(r"解释|上次|以前|比如|例如", text):
        return {}, None
    if not any(m["kind"] in _SOURCE_FIELDS for m in _SOUP.finditer(text)):
        return {}, None
    if (
        re.search(r"如果|假如|或者|还是|是否|吗|不是|[?？]", text)
        or re.search(rf"(?:不要|不用|不需要){_NUMBER}(?:道|个|款)?{_SOURCE_SOUP_NAME}", text)
        or re.search(rf"{_NUMBER}(?:到|至|[-~～]){_NUMBER}(?:道|个|款)?{_SOURCE_SOUP_NAME}", text)
    ):
        return {}, "请明确荤汤、素汤各几道及总汤数；素汤需完整的无肉来源证据，允许蛋奶。"
    counts, _, issue = _soup_count_evidence(text)
    return counts, issue


def explicit_menu_structure(message: str) -> tuple[dict[str, int], str | None]:
    """Return literal counts, or ask about ambiguity instead of guessing.

    'Four dishes including one soup' has four total items; 'four dishes and
    one soup' has five. Bare measures followed by component roles or food
    nouns do not become absolute totals. Qualified soups remain soup counts,
    not entree quantities. Ordinals, quoted examples and unrecognized
    expressions are left to the existing intent parser.
    """
    text = re.sub(r"\s+", "", message)
    if any(char in text for char in "“”‘’\"'「」") or re.search(r"换|解释|上次|以前", text):
        return {}, None
    raw_separate = list(_SEPARATE.finditer(text))
    if any(
        _has_total_prefix(text, m.start()) and m["link"] in {"加", "+", "和", "另加"}
        for m in raw_separate
    ):
        return {}, _ISSUE
    separate = [m for m in raw_separate if not _has_total_prefix(text, m.start())]
    totals = [
        match
        for match in _TOTAL.finditer(text)
        if not any(start.start() <= match.start() < start.end() for start in separate)
    ]
    soups = list(_SOUP.finditer(text))
    no_soup = re.search(r"(?:不要|不用|不需要|无需|不安排|无)汤", text)
    if not separate and not totals and not soups and not no_soup:
        return {}, None
    if (
        re.search(r"如果|假如|比如|例如|或者|还是|是否|吗|[?？]", text)
        or _negated_count_clause(text)
        or re.search(rf"{_NUMBER}(?:到|至|[-~～]){_NUMBER}(?:道|菜|汤)", text)
        or re.search(rf"(?:不要|不用|不需要){_NUMBER}(?:道|菜|汤)", text)
    ):
        return {}, _ISSUE
    try:
        total_counts = {_number(match["count"]) for match in totals}
        _, soup_counts, soup_issue = _soup_count_evidence(text)
        if soup_issue:
            return {}, soup_issue
        total_counts.update(
            _number(match["dishes"]) + next(iter(soup_counts)) for match in separate
        )
    except ValueError:
        return {}, _ISSUE
    if len(total_counts) > 1 or len(soup_counts) > 1:
        return {}, _ISSUE
    counts = {}
    if total_counts:
        counts["dish_count"] = total_counts.pop()
    if soup_counts:
        counts["soup_count"] = soup_counts.pop()
    if not 1 <= counts.get("dish_count", 1) <= 8 or not 0 <= counts.get("soup_count", 0) <= 3:
        return {}, "当前支持 1–8 道菜、0–3 道汤，请明确范围内的菜单结构。"
    if counts.get("soup_count", 0) > counts.get("dish_count", 8):
        return {}, _ISSUE
    return counts, None
