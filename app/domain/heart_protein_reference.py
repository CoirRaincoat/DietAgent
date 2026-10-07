"""Finite protein-body choices for a heart-oriented pattern, not nutrient scores.

These references never certify fat, sodium, servings, taste or a health effect.
Red meat is not forbidden; incomplete/unsupported bodies get no positive credit.
"""

import re
from collections.abc import Callable, Sequence

from app.domain.cooking_methods import main_cooking_methods
from app.domain.dish_composition import known_no_meat_food
from app.domain.dish_roles import has_protein_ingredient
from app.domain.food_names import without_plant_animal_homonyms
from app.domain.health_evidence import HealthEvidence, HealthRule, health_evidence
from app.domain.light_preparation import light_preparation_evidence
from app.domain.models import Recipe
from app.domain.protein_food_names import declared_protein_foods, whole_food_pattern
from app.domain.protein_food_references import named_protein_foods

VERSION = "source-heart-protein-body-v1"
_RED = frozenset({"猪肉", "牛肉"})
_LEAN_CHICKEN = whole_food_pattern(("鸡胸肉", "鸡胸", "鸡脯肉", "鸡脯", "去皮鸡肉"))
_ORDINARY_SODIUM = frozenset(
    {"盐", "食盐", "海盐", "生抽", "老抽", "酱油", "豉油", "蒸鱼豉油"}
)
_SWEET_OR_FATTY = HealthRule(
    discourage_terms=(
        "糖",
        "蜂蜜",
        "糖浆",
        "炼乳",
        "炼奶",
        "猪油",
        "黄油",
        "奶油",
        "椰子油",
        "棕榈油",
        "五花肉",
        "猪肥肉",
        "火腿",
        "培根",
        "腊肉",
        "腊肠",
        "香肠",
        "咸肉",
        "咸蛋",
    )
)
_UNRESOLVED_SEASONING = HealthRule(
    # Withhold positive body replacement for unspecified commercial mixtures;
    # this is not a claim that they are necessarily spicy, sugary or unsafe.
    discourage_terms=("烧烤粉", "烧烤汁", "照烧汁"),
)
_PUREE = re.compile(r"(?:打成|搅打成|压成|压碎成|碾成|搅成)(?:鱼|肉|细)?泥")
_BODY_CAUTION = re.compile(
    r"(?:猪|牛|羊|鸡|鸭)?肉(?:末|馅)|猪肉|牛肉|羊肉|鸡腿|鸡皮|鸭肉|鸭皮"
)
_NEGATED = re.compile(
    r"(?:不要|不用|无需|不必|避免|不|未)(?:再)?(?:加入|加|放入|放|使用)?$"
)


def _source_action(steps: str, pattern: re.Pattern[str]) -> bool:
    # Narrow source action reference, not an inferred texture/serving amount.
    for clause in re.split(r"[，,。；;\n]", steps):
        clause = re.sub(r'“[^”]*”|「[^」]*」|"[^"]*"', "", clause)
        if pattern is _BODY_CAUTION:
            clause = without_plant_animal_homonyms(clause)
        if re.search(r"例如|示例|有人说|解释|是否|[?？]", clause):
            continue
        for match in pattern.finditer(clause):
            if not _NEGATED.search(clause[: match.start()].strip()):
                return True
    return False


def preferred_protein_body(recipe: Recipe) -> bool:
    """Require a named/declared protein body and inspect the whole source.

    Fish/tofu or explicitly named breast/skinless chicken, not stock, sauce,
    a binder, a processed-meat compound or an unspecified mince. This narrow
    replacement reference also excludes puree finishes and whole-preparation
    light cautions; it does not ban those dishes or call them unhealthy.
    """
    named = named_protein_foods(recipe)
    if not named & {"鱼", "豆腐", "鸡肉"} or not main_cooking_methods(recipe):
        return False
    if not light_preparation_evidence(recipe).reference_usable:
        return False
    if health_evidence(recipe, _SWEET_OR_FATTY).has_caution:
        return False
    if health_evidence(recipe, _UNRESOLVED_SEASONING).has_caution:
        return False
    if _source_action(recipe.steps, _BODY_CAUTION):
        return False
    if "泥" in recipe.name:
        return False
    if _source_action(recipe.steps, _PUREE):
        return False
    declared: set[str] = set()
    lean_chicken = False
    for ingredient in recipe.ingredients:
        food = "".join(ingredient.name.split())
        families = declared_protein_foods(food)
        declared.update(families)
        if "鸡肉" in families:
            if "带皮" in food or _LEAN_CHICKEN.fullmatch(food) is None:
                return False
            lean_chicken = True
        if (
            has_protein_ingredient(food)
            and not families
            and not known_no_meat_food(food)
        ):
            return False
    if declared & _RED or not declared <= {"鱼", "虾", "豆腐", "鸡肉", "鸡蛋"}:
        return False
    return bool(named & {"鱼", "豆腐"} or "鸡肉" in named and lean_chicken)


def protein_body_tradeoff(
    previous: Recipe,
    candidate: Recipe,
    goals: Sequence[str],
    evidence: Callable[[Recipe, str], HealthEvidence | None],
) -> bool:
    """Compare supported bodies without treating added salt as a measured dose.

    Only a requested heart goal and a known red-meat body authorize this
    change. Every newly declared caution must be an ordinary sodium seasoning;
    sugar/fat/processed meat, unknown preparation and other goal conflicts
    do not acquire an exception. The caller retains non-pattern goal scores,
    explicit food/method/scene/flavor/count/local boundaries and hard rules.
    """
    if "护心" not in goals or not named_protein_foods(previous) & _RED:
        return False
    if not preferred_protein_body(candidate):
        return False
    return _ordinary_sodium_caution_tradeoff(previous, candidate, goals, evidence)


def named_food_tradeoff(
    previous: Recipe,
    candidate: Recipe,
    goals: Sequence[str],
    requested: Sequence[str],
    evidence: Callable[[Recipe, str], HealthEvidence | None],
) -> bool:
    """Honor an explicit fish/tofu dish preference, not a generic health score.

    The caller must strictly add a missing menu-level named reference and
    preserve all other goal/food/role/edit boundaries. No heart/BP goal or no
    explicit supported preference means no exception. A salt/soy declaration
    is disclosed, never equated with a measured sodium dose or ignored for
    hard safety. Complete supported preparations only, not soup/stock, puree
    or a carrot binder used to justify the whole meat dish.
    """
    if not set(goals) & {"护心", "降压"}:
        return False
    added = (
        (named_protein_foods(candidate) - named_protein_foods(previous))
        & set(requested)
        & {"鱼", "豆腐"}
    )
    if (
        not added
        or not preferred_protein_body(candidate)
        or any(
            evidence(record, goal) is None
            for record in (previous, candidate)
            for goal in dict.fromkeys(goals)
            if goal in {"护心", "降压"}
        )
    ):
        return False
    return _ordinary_sodium_caution_tradeoff(previous, candidate, goals, evidence)


def _ordinary_sodium_caution_tradeoff(
    previous: Recipe,
    candidate: Recipe,
    goals: Sequence[str],
    evidence: Callable[[Recipe, str], HealthEvidence | None],
) -> bool:
    """Share the existing source caution gate; no new scoring or dose rule."""
    for goal in dict.fromkeys(goals):
        old, new = evidence(previous, goal), evidence(candidate, goal)
        if old is None or new is None:
            continue
        before = set(
            (
                *old.discouraged_foods,
                *old.raw_cautions,
                *old.step_cautions,
                *old.bad_methods,
            )
        )
        after = set(
            (
                *new.discouraged_foods,
                *new.raw_cautions,
                *new.step_cautions,
                *new.bad_methods,
            )
        )
        additions = after - before
        if additions and (
            goal not in {"护心", "降压"} or not additions <= _ORDINARY_SODIUM
        ):
            return False
    return True
