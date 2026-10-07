"""Finite manual-review cues, NOT recipe completion or nutrition certification."""

import re
from dataclasses import dataclass

from app.domain.models import Recipe

VERSION = "offline-source-preparation-review-v1"
_CLAUSES = re.compile(r"[。；;\n]+")
_DEVICE = re.compile(r"(?:选择|点击|按下|按)(?:[\"“”‘’\s]*)开始烹饪|按屏幕提示操作")
_PROGRAM = re.compile(
    r"(?:选择|设置|启动)[^。；;\n]{0,24}(?:蒸|炖|煮|烤|炒|煎|炸|焖|焯)"
    r"[\"“”‘’\s]*(?:模式|程序)"
    r"|设置[^。；;\n]{0,12}\d+\s*(?:分钟|min)[^。；;\n]{0,12}\d+\s*(?:℃|度|°C)"
)
_DIP = re.compile(
    r"(?:蘸|加入|淋上|淋入|倒入|拌入|加)(?:适量|少许|一点|少量|些|的|点)?"
    r"(?:酱料|酱汁|蘸料|调味料|复合调味料)"
)
_OMIT = re.compile(r"(?:不|不要|不用|无需|不必|免|别|没有|未|不建议|不要再|不再|无需再)$")
_MENTION = re.compile(r"例如|比如|举例|示例|为什么|是否|解释|[?？]")


@dataclass(frozen=True)
class PreparationReview:
    flags: tuple[str, ...]
    evidence: tuple[str, ...]
    status: str


def preparation_review(recipe: Recipe) -> PreparationReview:
    """Flag source passages for a person, without altering ranks or recipes.

    A finishing witness such as 蒸好 may establish an observed method without
    specifying a machine's actual program. An unqualified 酱料 addition is not
    a known salt/oil amount; this cue must not manufacture those ingredients.
    These bounded patterns are incomplete. No findings is not a certificate
    of executable preparation, suitable servings, food safety or health.
    """
    clauses = [part.strip() for part in _CLAUSES.split(recipe.steps) if part.strip()]
    flags, evidence = [], []
    actual = [part for part in clauses if not _MENTION.search(part)]
    if not any(
        not _OMIT.search(part[: match.start()])
        for part in actual
        for match in _PROGRAM.finditer(part)
    ):
        device = [
            part
            for part in actual
            if any(not _OMIT.search(part[: match.start()]) for match in _DEVICE.finditer(part))
        ]
        if device:
            flags.append("device_program_not_in_source_text")
            evidence.extend(device)
    dips = []
    for part in actual:
        for match in _DIP.finditer(part):
            if not _OMIT.search(part[: match.start()]):
                dips.append(part)
    if dips:
        flags.append("generic_sauce_addition_needs_composition_review")
        evidence.extend(dips)
    return PreparationReview(
        tuple(flags),
        tuple(dict.fromkeys(evidence)),
        "needs_manual_review" if flags else "no_finite_findings_NOT_certified",
    )
