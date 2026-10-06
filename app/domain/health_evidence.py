"""Finite source evidence for qualitative goal ordering, never nutrient doses."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache

from app.domain.cooking_methods import source_cooking_method_evidence
from app.domain.dish_roles import has_protein_ingredient, primary_dish_role
from app.domain.models import Recipe
from app.domain.protein_food_names import declared_protein_foods

HEALTH_EVIDENCE_VERSION = "source-health-preference-v5-sodium-reminder-split"
_CONDIMENTS = ("酱", "汁", "汤", "精", "调味", "料包", "油", "豆蔻", "肉桂", "肉蔻")
_FALSE_PROTEIN = ("蛋糕", "蛋挞", "牛油果", "鸡头米", "鸡血藤", "鸡冠花")
_PLANT_MILK_PROTEINS = (
    "黄豆",
    "大豆",
    "毛豆",
    "黑豆",
    "红豆",
    "绿豆",
    "豌豆",
    "鹰嘴豆",
    "牛奶",
    "鲜奶",
    "纯牛奶",
    "酸奶",
    "奶粉",
    "豆浆",
    "奶酪",
    "乳酪",
    "芝士",
)


@lru_cache(maxsize=8192)
def _compact(text: str) -> str:
    return "".join(text.split()).casefold()


def _contains(text: str, term: str) -> bool:
    text, term = _compact(text), _compact(term)
    if not term:
        return False
    if re.fullmatch(r"[a-z -]+", term):
        return re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text) is not None
    return term in text


@lru_cache(maxsize=256)
def _omission_pattern(term: str) -> re.Pattern[str]:
    omissions = rf"(?:不加|不放|不用|无需加|无需放|无需使用|无|免){re.escape(term)}"
    if term in {"蒸", "煮", "炖", "焯", "炸", "油炸", "油煎", "煎"}:
        optional_oil = "(?:油)?" if term == "炸" else ""
        omissions += rf"|(?:不|不做|不采用|不用|无需){optional_oil}{re.escape(term)}"
    return re.compile(omissions)


def _declares(text: str, term: str) -> bool:
    """Ignore only immediate finite omission phrases, not entire source clauses.

    Optional additions still count. A later positive mention still survives.
    This is not a general natural-language negation or brand-composition parser.
    """
    text = _compact(text)
    # Most declared terms have no omission marker. Skip regex work in that
    # common path, while still checking later positive mentions after removal.
    if any(marker in text for marker in ("不", "无", "免")):
        text = _omission_pattern(term).sub("", text)
    return _contains(text, term)


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        dict.fromkeys(item for item in value if isinstance(item, str) and item)
    )


@dataclass(frozen=True)
class HealthRule:
    """Rule configuration whose integer weights are engineering preferences."""

    prefer_categories: tuple[str, ...] = ()
    prefer_terms: tuple[str, ...] = ()
    discourage_terms: tuple[str, ...] = ()
    prefer_methods: tuple[str, ...] = ()
    discourage_methods: tuple[str, ...] = ()
    note: str = ""
    sources: tuple[str, ...] = ()
    positive_roles: tuple[str, ...] = ()
    scope_category_to_role: bool = False
    scope_preferred_to_role: bool = False
    attention_terms: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "HealthRule":
        """Read finite list fields without coercing malformed source values."""
        note = value.get("note", "")
        return cls(
            prefer_categories=_strings(value.get("prefer_categories")),
            prefer_terms=_strings(value.get("prefer_terms")),
            discourage_terms=_strings(value.get("discourage_terms")),
            prefer_methods=_strings(value.get("prefer_methods")),
            discourage_methods=_strings(value.get("discourage_methods")),
            note=note if isinstance(note, str) else "",
            sources=_strings(value.get("sources")),
            positive_roles=_strings(value.get("positive_roles")),
            scope_category_to_role=value.get("scope_category_to_role") is True,
            scope_preferred_to_role=value.get("scope_preferred_to_role") is True,
            attention_terms=_strings(value.get("attention_terms")),
        )


@dataclass(frozen=True)
class HealthEvidence:
    """Traceable positive foods and cautions, excluding titles and health labels."""

    category_foods: tuple[str, ...]
    preferred_foods: tuple[str, ...]
    discouraged_foods: tuple[str, ...]
    raw_cautions: tuple[str, ...]
    step_cautions: tuple[str, ...]
    good_methods: tuple[str, ...]
    bad_methods: tuple[str, ...]
    positive_rank_enabled: bool = True
    category_rank_enabled: bool = True
    attention_foods: tuple[str, ...] = ()
    raw_attention: tuple[str, ...] = ()
    step_attention: tuple[str, ...] = ()
    preferred_rank_terms: tuple[str, ...] | None = None

    @property
    def has_caution(self) -> bool:
        return self.has_rank_caution or self.has_attention

    @property
    def has_rank_caution(self) -> bool:
        return bool(
            self.discouraged_foods
            or self.raw_cautions
            or self.step_cautions
            or self.bad_methods
        )

    @property
    def has_attention(self) -> bool:
        return bool(self.attention_foods or self.raw_attention or self.step_attention)

    @property
    def has_preference(self) -> bool:
        return bool(self.category_foods or self.preferred_foods or self.good_methods)

    @property
    def score(self) -> int:
        """Return the configured qualitative preference, never a quality score."""
        food_caution = bool(
            self.discouraged_foods or self.raw_cautions or self.step_cautions
        )
        return (
            (
                2 * bool(self.category_foods) * self.category_rank_enabled
                + 2
                * bool(
                    self.preferred_foods
                    if self.preferred_rank_terms is None
                    else self.preferred_rank_terms
                )
                + bool(self.good_methods)
            )
            * self.positive_rank_enabled
            - 3 * food_caution
            - 2 * bool(self.bad_methods)
        )

    def score_with_covered_terms(self, covered: set[str]) -> int:
        """Saturate existing whole-food preference credit, never a quality score."""
        duplicate = (
            self.preferred_rank_terms is not None
            and bool(self.preferred_rank_terms)
            and set(self.preferred_rank_terms) <= covered
        )
        return self.score - 2 * int(duplicate) * self.positive_rank_enabled


def _protein(name: str) -> bool:
    return not any(
        _contains(name, marker) for marker in (*_CONDIMENTS, *_FALSE_PROTEIN)
    ) and (
        has_protein_ingredient(name)
        or any(_contains(name, food) for food in _PLANT_MILK_PROTEINS)
    )


@lru_cache(maxsize=8192)
def _source_role(
    name: str, names: tuple[str, ...], steps: str, label: str
) -> str | None:
    """Cache exact immutable source, never an ID, user fact or nutrient claim."""
    return primary_dish_role(name, names, steps, re.split(r"[、,，;；|\s]+", label))


def health_evidence(recipe: Recipe, rule: HealthRule) -> HealthEvidence:
    """Inspect actual ingredient names, raw declarations and preparation steps.

    Positive foods require parsed ingredient evidence. Cautions also inspect
    raw ingredients and steps, including optional additions. Experimental scoped
    category ranking requires exactly one cached culinary role agreeing with
    a fresh whole-source role: a vegetable
    does not turn rice, soup or a protein entree into a vegetable dish. Source
    foods remain available to explanations and nutrition analysis even when
    they cannot earn a cross-role category bonus. Category metadata cannot
    award points by itself. Positive methods require a supported source
    finishing action, not equipment, optional choices or an earlier preparation
    boil. Frying cautions retain explicit earlier/topping/optional actions;
    generic pan-frying never establishes oil frying or an oil amount.
    No absence of a caution establishes a low-sodium/low-sugar certification.
    """
    names = tuple(dict.fromkeys(item.name for item in recipe.ingredients if item.name))
    source_role = (
        _source_role(recipe.name, names, recipe.steps, recipe.raw_label)
        if rule.scope_category_to_role or rule.scope_preferred_to_role
        else None
    )
    return _source_health_evidence(
        names,
        recipe.raw_ingredients,
        recipe.steps,
        rule,
        tuple(recipe.categories),
        source_role,
    )


@lru_cache(maxsize=8192)
def _source_health_evidence(
    names: tuple[str, ...],
    raw_ingredients: str,
    steps: str,
    rule: HealthRule,
    roles: tuple[str, ...],
    source_role: str | None,
) -> HealthEvidence:
    # Immutable source text, the complete frozen rule and the exact role tuple
    # and fresh source role form this cache key. Roles restrict positive ranking,
    # not source evidence. Title/finish/source-label changes that alter that role
    # cannot reuse a cached category bonus. Unscoped legacy ordering is unchanged.
    # IDs and user/session facts never enter it. Source/rule/role changes cannot
    # reuse stale goal evidence.
    category_foods: list[str] = []
    for name in names:
        if "protein" in rule.prefer_categories and _protein(name):
            category_foods.append(name)
        elif not any(_contains(name, marker) for marker in _CONDIMENTS):
            role = primary_dish_role("", [name], "", [])
            if role in rule.prefer_categories:
                category_foods.append(name)
    preferred = tuple(
        name
        for name in names
        if not any(_contains(name, marker) for marker in _CONDIMENTS)
        and any(_contains(name, term) for term in rule.prefer_terms)
        and ("protein" not in rule.prefer_categories or _protein(name))
        and not any(_contains(name, term) for term in ("燕麦奶", "蛋糕", "饼干"))
    )
    # E.g. muscle-oriented ranking is restricted to a verified protein-entrée
    # slot. An incidental egg in a staple must not churn the rice/vegetable
    # slots merely to maximize source-token points. Actual nutrition sources
    # remain independently listed by the nutrient-source analysis.
    positive_rank_enabled = not rule.positive_roles or bool(
        set(rule.positive_roles).intersection(roles)
    )
    category_rank_enabled = not rule.scope_category_to_role or (
        len(roles) == 1
        and roles[0] == source_role
        and source_role in rule.prefer_categories
    )
    ranking_terms = tuple(
        term for term in rule.discourage_terms if term not in rule.attention_terms
    )
    discouraged = tuple(
        name for name in names if any(_declares(name, term) for term in ranking_terms)
    )
    parsed_terms = {
        term
        for term in ranking_terms
        if any(_declares(name, term) for name in discouraged)
    }
    raw_cautions = tuple(
        term
        for term in ranking_terms
        if term not in parsed_terms and _declares(raw_ingredients, term)
    )
    step_cautions = tuple(
        term
        for term in ranking_terms
        if term not in parsed_terms
        and term not in raw_cautions
        and _declares(steps, term)
    )
    methods = source_cooking_method_evidence(steps)
    attention = tuple(
        name
        for name in names
        if any(_declares(name, term) for term in rule.attention_terms)
    )
    parsed_attention = {
        term
        for term in rule.attention_terms
        if any(_declares(name, term) for name in attention)
    }
    raw_attention = tuple(
        term
        for term in rule.attention_terms
        if term not in parsed_attention and _declares(raw_ingredients, term)
    )
    step_attention = tuple(
        term
        for term in rule.attention_terms
        if term not in parsed_attention
        and term not in raw_attention
        and _declares(steps, term)
    )
    preferred_rank_terms = None
    if rule.scope_preferred_to_role:
        preferred_rank_terms = tuple(
            term
            for term in rule.prefer_terms
            if len(roles) == 1
            and roles[0] == source_role
            and any(
                _contains(name, term)
                and primary_dish_role("", [name], "", []) == source_role
                and (term != "豆腐" or "豆腐" in declared_protein_foods(name))
                for name in preferred
            )
        )
    return HealthEvidence(
        tuple(dict.fromkeys(category_foods)),
        preferred,
        discouraged,
        raw_cautions,
        step_cautions,
        tuple(
            method for method in rule.prefer_methods if method in methods.main_methods
        ),
        tuple(
            method
            for method in rule.discourage_methods
            if any(
                event.role
                in {
                    "candidate",
                    "finished_witness",
                    "auxiliary",
                    "preparation",
                    "optional",
                }
                and _declares(event.text, method)
                for event in methods.events
            )
        ),
        positive_rank_enabled,
        category_rank_enabled,
        attention,
        raw_attention,
        step_attention,
        preferred_rank_terms,
    )


def no_goal_regression(candidate: Sequence[int], previous: Sequence[int]) -> bool:
    """Compare every requested goal separately, without hiding conflicts in a sum."""
    return len(candidate) == len(previous) and all(
        a >= b for a, b in zip(candidate, previous)
    )
