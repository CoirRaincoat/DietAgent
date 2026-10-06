"""Finite source-bound sauce references, not inferred brand composition."""

import re
from collections.abc import Iterable
from functools import lru_cache

from app.domain.models import Recipe

VERSION = "source-unqualified-sauce-v4-barbecue-components"
_NAMED_COMPOSITES = frozenset({"照烧汁", "烧烤粉", "烧烤汁", "烧烤酱"})
# Only a complete bare sauce reference, not the prefix of 酱油/酱牛肉.
_NOUN = r"(?:照烧汁|烧烤粉|烧烤汁|烧烤酱|蘸酱汁|酱料|酱汁|蘸料|调味汁|料汁|酱(?=即可|食用|享用|吃|由|[。；;，,]|$))"
_QUALIFIER = r"(?:上述|前述|备好[的]?|准备好[的]?|调好[的]?|自制[的]?)?"
_USE = re.compile(
    r"(?:蘸|加入|淋上|淋入|倒入|拌入|拌上|抹上|涂上|刷上|撒上|撒入|搭配|配|加)"
    r"(?:适量|少许|一点|少量|些|的|点)?" + _QUALIFIER + r"(?P<noun>" + _NOUN + r")"
)
_DEFINE = re.compile(
    r"(?P<forward>" + _NOUN + r")由(?P<list1>[^。；;\n]+?)(?:组成|调成|制成)"
    r"|(?:将|把|加入|放入|称取|加)(?P<list2>[^。；;\n]+?)"
    r"(?:调成|拌成|制成|制好|作|做|混匀为|混合均匀成)(?P<reverse>" + _NOUN + r")"
)
_OMIT = re.compile(
    r"(?:不|不要|不用|无需|不必|免|别|没有|未|不建议|不要再|不再|无需再)$"
)
_DISCUSSION = re.compile(r"例如|比如|举例|示例|为什么|是否|解释|[?？]")
_SEPARATOR = re.compile(r"[。；;\n]+")
_ATOM_SEPARATOR = re.compile(r"[、，,]|和|与|及")
_BARE = re.compile(_QUALIFIER + r"(?P<noun>" + _NOUN + r")")
_PREPARING_NOUN = re.compile(r"(?:做|作|制成|制好|调成|拌成|制作|准备)$")
_TAIL_ACTION = re.compile(
    r"(?:[,，]?(?:充分)?(?:搅拌均匀|混合均匀|混合|一起拌匀|拌匀|取下小碗|取下碗))[,，]?$"
)
_AMOUNT = r"\d+(?:\.\d+)?(?:/\d+)?(?:毫升|ml|克|g|小勺|大勺|勺)"
_NEW_PREPARATION = re.compile(r"[,，](?:之后|随后|然后|再取|另取)")


def _listed_foods(text: str) -> list[str]:
    text = text.rstrip(",，")
    while match := _TAIL_ACTION.search(text):
        text = text[: match.start()].rstrip(",，")
    atoms = _ATOM_SEPARATOR.split(text.casefold())
    return [
        re.sub(r"(?:" + _AMOUNT + r")$", "", re.sub(r"^(?:" + _AMOUNT + r")", "", atom))
        for atom in atoms
    ]


def unresolved_sauce_evidence(recipe: Recipe, known_foods: Iterable[str]) -> list[str]:
    """Find unknown generic/named components and literal sauce/powder uses.

    Only a preceding positive, exhaustive finite list of known literal foods
    resolves that exact noun. An example, other sauce, later definition, unknown
    atom or 等 does not. This is not general reference resolution, a medical
    safety certificate or a promise about a commercial product's formulation.
    Explicit allergen/chili checks still examine all source ingredients/steps.
    No metadata, fingerprint or remembered result supplies missing composition.
    """
    return list(
        _source_evidence(
            recipe.steps,
            tuple((item.name, item.raw) for item in recipe.ingredients),
            _normalized_known(frozenset(known_foods)),
        )
    )


@lru_cache(maxsize=64)
def _normalized_known(values: frozenset[str]) -> frozenset[str]:
    return frozenset(re.sub(r"\s+", "", item).casefold() for item in values)


@lru_cache(maxsize=8192)
def _source_evidence(
    steps: str, ingredients: tuple[tuple[str, str], ...], known: frozenset[str]
) -> tuple[str, ...]:
    # Cache keys include actual source text, declarations and vocabulary, not
    # IDs, metadata or fingerprints. Return immutable evidence to callers.
    resolved: dict[str, bool] = {}
    evidence: list[str] = []
    for source in _SEPARATOR.split(steps):
        clause = re.sub(r"\s+", "", source)
        if not clause or _DISCUSSION.search(clause):
            continue
        # A literal transition starts a new preparation, not an arbitrary
        # suffix of an ingredient list. This prevents an earlier 炒香 action
        # swallowing a later explicitly listed sauce; repeated 加入 alone
        # cannot hide an unknown atom in the same preparation.
        starts = [0, *(match.end() for match in _NEW_PREPARATION.finditer(clause))]
        ends = [
            *(match.start() for match in _NEW_PREPARATION.finditer(clause)),
            len(clause),
        ]
        events = [
            (start + match.start(), "define", match)
            for start, end in zip(starts, ends, strict=True)
            for match in _DEFINE.finditer(clause[start:end])
        ]
        events += [(match.start(), "use", match) for match in _USE.finditer(clause)]
        for position, kind, match in sorted(events, key=lambda item: item[0]):
            if _OMIT.search(clause[:position]):
                continue
            if kind == "define":
                noun = match.group("forward") or match.group("reverse")
                listed = match.group("list1") or match.group("list2")
                atoms = _listed_foods(listed)
                resolved[noun] = bool(atoms) and all(
                    atom and atom in known for atom in atoms
                )
            else:
                if _PREPARING_NOUN.search(clause[: match.start()]):
                    continue
                # An explicit prior-reference qualifier can bind a different
                # generic label only when exactly one fully declared sauce
                # exists. Bare other nouns or ambiguous definitions cannot.
                referenced = re.search(
                    r"上述|前述|备好的|准备好的|调好的", match.group()
                )
                unique_reference = (
                    bool(referenced)
                    and match.group("noun") not in _NAMED_COMPOSITES
                    and len(resolved) == 1
                    and all(resolved.values())
                )
                if (
                    not resolved.get(match.group("noun"), False)
                    and not unique_reference
                ):
                    evidence.append(source.strip())
    for ingredient_name, ingredient_raw in ingredients:
        name = re.sub(r"\s+", "", ingredient_name)
        ingredient_match = _BARE.fullmatch(name)
        if ingredient_match and not resolved.get(ingredient_match.group("noun"), False):
            evidence.append("原料：" + ingredient_raw)
    return tuple(dict.fromkeys(evidence))
