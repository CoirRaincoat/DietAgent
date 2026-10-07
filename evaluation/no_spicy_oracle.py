"""Frozen acceptance vocabulary, independently authored from culinary counterexamples.

Do not import production rules/configuration here. This supplements older case
vocabularies without changing historical dataset bytes or claiming full coverage.
"""

import re
from collections.abc import Iterable

ORACLE_VERSION = "non-spicy-source-oracle-v2-mustard-color-pepper"
CHILI_TERMS = (
    "辣椒",
    "辣油",
    "辣酱",
    "小米椒",
    "小米辣",
    "油泼辣子",
    "辣子油",
    "糍粑辣子",
    "朝天椒",
    "剁椒",
    "泡椒",
    "干红椒",
    "红油",
    "豆瓣酱",
    "哈瓦那椒",
    "辣粉",
    "青尖椒",
    "尖椒",
    "杭椒",
    "美人椒",
    "二荆条",
    "泡红椒",
    "线椒",
    "芥末",
)
CHILI_LABELS = frozenset({"辣", "微辣", "香辣", "酸辣"})
UNVERIFIED_COMPOUNDS = (
    "调味料",
    "调料包",
    "酱料包",
    "火锅底料",
    "咖喱块",
    "浓汤宝",
    "复合调味料",
    "沙拉酱",
    "沙茶酱",
    "蛋白粉",
    "不详",
    "未知",
)


def no_spicy_findings(text: str, labels: Iterable[str] = ()) -> list[str]:
    """Audit written source/visible evidence, not returned suitability flags.

    Args:
        text: Ingredients and steps; titles alone are not ingredient evidence.
        labels: Exact descriptive labels or visible badges.

    Returns:
        Concrete chili or uncertainty evidence. Passing this finite vocabulary
        does not certify every brand formulation or individual flavor tolerance.
    """
    compact_text = re.sub(r"\s+", "", text).casefold()
    evidence = [term for term in CHILI_TERMS if term in compact_text]
    evidence.extend(f"辣味标签:{label}" for label in sorted(set(labels) & CHILI_LABELS))
    evidence.extend(f"成分不明:{term}" for term in UNVERIFIED_COMPOUNDS if term in compact_text)
    # This finite audit intentionally has no production imports/config access.
    # Color alone does not establish a mild cultivar; only a directly bound
    # source declaration resolves that same food, not a separate sweet pepper.
    direct_types = set(re.findall(
        r"(青红椒|青椒|红椒)[（(](?:[青红黄绿](?:色)?)?(?:甜椒|彩椒|柿子椒)[）)]",
        compact_text,
    ))
    evidence.extend(
        f"椒品种未明确:{food}" for food in re.findall(r"青红椒|青椒|红椒", compact_text)
        if food not in direct_types
    )
    return sorted(set(evidence))
