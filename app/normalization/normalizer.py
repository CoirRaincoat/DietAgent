"""Conservative extraction of single food terms; unknowns never disappear.

Adapted from the supplied archive. The rule engine is the only food/allergen
dictionary. Approximate or embedding matches cannot establish allergy safety.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.agent.clarification import asserted_context
from app.domain.models import Constraints
from app.rules.engine import RuleEngine


@dataclass(frozen=True)
class TermEvidence:
    raw: str
    cleaned: str
    canonical: str | None


@dataclass(frozen=True)
class NormalizationResult:
    known_terms: list[str]
    unknown_terms: list[str]
    evidence: list[TermEvidence]


class TermNormalizer:
    def __init__(self, rules: RuleEngine | None = None):
        self.rules = rules or RuleEngine()

    @staticmethod
    def _clean_phrase(value: str) -> str:
        value = unicodedata.normalize("NFKC", value).strip(" ，,。；;：:！!")
        allergy = re.fullmatch(r"(?:对)?(.+?)(?:过敏|不耐受)(?:了)?", value)
        exclusion = re.fullmatch(r"(?:不能吃|不吃|忌吃|忌口[:：]?|不要吃|别吃)(.+)", value)
        if allergy or exclusion:
            value = (allergy or exclusion)[1].strip()
        return value

    def normalize_terms(
        self, candidates: Iterable[str], *, kind: Literal["food", "allergy"] = "food",
    ) -> NormalizationResult:
        if kind not in {"food", "allergy"}:
            raise ValueError("Unsupported normalization kind")
        known: list[str] = []
        unknown: list[str] = []
        evidence: list[TermEvidence] = []
        for raw in candidates:
            if not isinstance(raw, str):
                raise TypeError("Food terms must be strings")
            if not raw.strip():
                continue
            cleaned = self._clean_phrase(raw)
            canonical = self.rules.canonical_food(cleaned)
            constraints = Constraints(**{
                "allergies" if kind == "allergy" else "excluded_ingredients": [canonical],
            })
            unresolved = (
                self.rules.unresolved_allergies(constraints) if kind == "allergy"
                else self.rules.unresolved_exclusions(constraints)
            )
            if not cleaned or not asserted_context(raw) or unresolved:
                unknown.append(raw)
                evidence.append(TermEvidence(raw, cleaned, None))
            else:
                known.append(canonical)
                evidence.append(TermEvidence(raw, cleaned, canonical))
        return NormalizationResult(
            list(dict.fromkeys(known)), list(dict.fromkeys(unknown)), evidence,
        )
