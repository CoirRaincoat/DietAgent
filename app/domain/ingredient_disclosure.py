"""Informational source notices, never allergy clearance or ranking changes."""
import re
from collections.abc import Sequence
from functools import lru_cache

from app.domain.models import Recipe

VERSION = "source-egg-and-brand-disclosure-v1"
_EGG = re.compile(r"鹌鹑蛋|鸡蛋|鸭蛋|鹅蛋|蛋清|蛋黄|蛋液|蛋粉|皮蛋|松花蛋|咸蛋|蛋白(?!质|酶|粉)")
_BRAND = re.compile(r"蒸鱼豉油|生抽|老抽|酱油|豉油|蚝油|鸡精|鸡粉|鸡汁|高汤块|浓汤宝|香肠|腊肠|火腿")
_QUOTES = re.compile(r"“[^”]*”|「[^」]*」|‘[^’]*’|\"[^\"]*\"|'[^']*'")
_OMIT = re.compile(r"(?:不加|不要|不用|无需|不必|不放|勿加|不含|不使用)\s*$")
_NON_EGG_PROTEIN = re.compile(r"(?:植物|大豆|豌豆|小麦|乳清|胶原|酪)蛋白")


@lru_cache(maxsize=8192)
def _source_terms(raw: str, names: tuple[str, ...], steps: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    # No recipe title, tags or assumed brand composition. Remove quoted
    # examples and locally negated mentions; actual declared items still count.
    text = _NON_EGG_PROTEIN.sub("", "\n".join((raw, *names, _QUOTES.sub("", steps))))
    found = []
    for pattern in (_EGG, _BRAND):
        terms = []
        for match in pattern.finditer(text):
            if not _OMIT.search(text[:match.start()]):
                terms.append(match.group())
        found.append(tuple(dict.fromkeys(terms)))
    return found[0], found[1]


def ingredient_disclosures(recipe: Recipe) -> list[str]:
    """Name finite declared egg sources and brands requiring label review.

    This is not an exhaustive allergen detector. RuleEngine independently
    retains all existing explicit allergy, unknown-sauce and dietary gates.
    A warning does not assert a product contains an undeclared allergen.
    """
    eggs, brands = _source_terms(recipe.raw_ingredients, tuple(i.name for i in recipe.ingredients), recipe.steps)
    notices = []
    if eggs:
        notices.append(f"{recipe.name}：原方含{'、'.join(eggs)}，属于蛋类来源；鸡蛋过敏者不能照用。")
    if brands:
        notices.append(
            f"{recipe.name}：{'、'.join(brands)}的品牌及完整配方未核；"
            "请核对包装配料表、过敏原声明及交叉接触提示，不能仅凭名称认定含有或不含某种过敏原。"
        )
    return notices


def ingredient_safety_copy(chosen: Sequence[Recipe]) -> str | None:
    notices = [notice for recipe in chosen for notice in ingredient_disclosures(recipe)]
    if not notices:
        return None
    return "食材核对提醒：\n" + "\n".join(notices) + "\n如已知过敏，须确认实际配料符合要求；不能确认时不要使用该调料。此清单不覆盖所有过敏原，未认证整餐过敏安全。"
