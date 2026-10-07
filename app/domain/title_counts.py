"""Finite zero-soup title metadata, never a rewritten source recipe name."""

import re

TITLE_COUNT_VERSION = "culinary-zero-soup-title-v1"
_NUMERALS = "0-9０-９零〇一二两三四五六七八九十"
_ZERO = "[0０零〇]"
_SOUP_WORD_PART = "[匙勺汁底料粉圆]"
_SINGLE_NO_SOUP = re.compile(
    rf"(?<![{_NUMERALS}.．])(?:1|１|一)(?:道)?菜[、，,＋+]?(?:{_ZERO})(?:道)?汤(?!{_SOUP_WORD_PART})"
)
_ZERO_SOUP = re.compile(rf"(?<![{_NUMERALS}.．菜]){_ZERO}(?:道)?汤(?!{_SOUP_WORD_PART})")
_OTHER_PAIR = re.compile(
    rf"[{_NUMERALS}]+(?:道)?菜[、，,＋+]?{_ZERO}(?:道)?汤(?!{_SOUP_WORD_PART})"
)
_EMPTY_BRACKETS = re.compile(r"\(\)|（）|【】|\[\]")


def culinary_title_without_zero_soup_metadata(name: str) -> str:
    """Ignore explicit zero-soup notes when deriving a culinary role.

    Only one-dish/zero-soup pairs and isolated zero-soup count tokens are
    removed from a temporary classification view. Multi-dish counts, nonzero
    soup counts, decimal/longer numeral tails and soup word parts stay intact.
    An actual soup name elsewhere in the title remains soup evidence. This
    neither resolves contradictory source claims nor overrides user requests,
    source identity, ingredients, preparation or hard-constraint decisions.
    """
    title = "".join(name.split())
    title = _SINGLE_NO_SOUP.sub("", title)
    # Do not strip only the zero-soup half of a multi/unknown dish-count pair,
    # including pairs with punctuation separating their two counts.
    pairs = tuple((match.start(), match.end()) for match in _OTHER_PAIR.finditer(title))
    title = _ZERO_SOUP.sub(
        lambda match: match.group() if any(a <= match.start() < b for a, b in pairs) else "", title
    )
    return _EMPTY_BRACKETS.sub("", title).strip("·、，,；;:-— ")
