"""Finite source finishing actions, distinct from equipment and preparation."""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from app.domain.models import Recipe

METHOD_VERSION = "source-finishing-method-v5"
_ALIASES = {
    "蒸": "蒸",
    "清蒸": "蒸",
    "蒸制": "蒸",
    "煮": "煮",
    "熬煮": "煮",
    "烧煮": "煮",
    "炖": "炖",
    "炖煮": "炖",
    "炒": "炒",
    "翻炒": "炒",
    "煸炒": "炒",
    "烤": "烤",
    "烧烤": "烤",
    "烘烤": "烤",
    "烘焙": "烤",
    "煎": "煎",
    "油煎": "煎",
    "炸": "炸",
    "油炸": "炸",
    "焖": "焖",
    "焖煮": "焖",
    "红烧": "烧",
    "烧": "烧",
    "拌": "拌",
    "凉拌": "拌",
    "冷拌": "拌",
    "榨汁": "榨汁",
    "焯": "焯",
    "焯水": "焯",
}
_ACTION = re.compile("|".join(sorted(map(re.escape, _ALIASES), key=len, reverse=True)))
_EQUIPMENT = re.compile(
    r"蒸烤(?:架|盘|箱|炉)|烤肠模具|蒸(?:盘|锅|架|笼|箱|盖|鱼豉油|肉粉)"
    r"|烤(?:箱|盘|架|炉|网|麸)|炒锅|煎锅|(?:空气|油)?炸锅|搅拌(?:棒|刀|机)"
)
_SEPARATOR = re.compile(r"[，,。；;\n]")
_AUXILIARY = re.compile(r"酱汁|调味汁|料汁|汁液|汁水|芡汁|浇头|蒜蓉|蒜末|热油")
_BODY = re.compile(r"食材|豆腐|鸡肉|猪肉|牛肉|排骨|白菜|南瓜|蔬菜|鱼|饼|米饭|粥|面条")
_SERVE = re.compile(r"装盘|食用|享用|品尝|即可")
_PLATED = re.compile(r"装盘|摆入盘中|摆盘|摆放在盘中")
_UNFINISHED = re.compile(r"馅料|肉馅|面团|包饺|入模|备用|保存")
_EXECUTION = re.compile(r"开始烹饪|启动机器")
_STEAM_DEVICE = re.compile(r"蒸箱|蒸锅")
_NEGATION = re.compile(r"(?:不要|不用|无需|不必|不采用|不能|不再|尚未|没有|不)(?:再|进行|使用)?$")
_OPTIONAL = re.compile(r"(?:可以|也可|可选|可用)$")
_PREPARED_PREFIX = re.compile(r"(?:买来的|购来的|现成|熟制)[^，,。；;\n]{0,32}$")
_PREPARED_NOUN = re.compile(r"^(?:饺(?:子)?|饭|面包)(?=取出|摆盘|装盘|切|放入|$)")
_DRAINED_BODY = re.compile(r"^(?:沥干|捞出|取出)(?:后)?(?:再)?(?:与|和|同)")
_SEASONING_ADDITION = re.compile(
    r"^入(?:少许|适量)?(?:孜然(?:粉)?|胡椒粉|辣椒粉|盐|白糖|糖|醋|生抽|老抽|酱油)"
    r"(?=后|装盘|即可|$)"
)
_COLD_MIX = re.compile(r"放凉|冷却|冷食|凉拌|冷拌")
_DRAINED_PREPARATION = re.compile(r"^(?:水|(?:至)?熟)?(?:后)?[，,\s]*$")
_SEASONING_AFTER_HEAT = re.compile(
    r"^(?:至)?(?:熟|软|透|金黄|上色|酥脆)(?:后|再|然后|随后|装盘|出锅|[，,。；;\s])*$"
)


def _affirmative_pour(text: str) -> bool:
    """A literal later pouring action, not a negated/optional suggestion."""
    for match in re.finditer(r"(?:淋|浇)(?:在|到|上|入)", text):
        left = max((item.end() for item in _SEPARATOR.finditer(text[:match.start()])), default=0)
        prefix = text[left:match.start()]
        if not _NEGATION.search(prefix) and not _OPTIONAL.search(prefix):
            return True
    return False


def _poured_liquid_role(steps: str, left: int, action: int, right: int) -> str | None:
    """Bind heating to a literal liquid object in this/one adjacent clause.

    Extending a lookahead alone can misclassify a steam programme as a sauce.
    Only named oil/juice/water immediately before heating supplies this link.
    """
    prefix = steps[left:action]
    if not prefix.strip() and left and steps[left - 1] in "，,":
        previous = max((match.end() for match in _SEPARATOR.finditer(steps[:left - 1])), default=0)
        prefix = steps[previous:action]
    if _BODY.search(prefix) or _NEGATION.search(prefix):
        return None
    liquid = re.search(r"(?P<kind>汁|水|油)[，,\s]*$", prefix)
    if not liquid or not _affirmative_pour(steps[action:right + 160]):
        return None
    return liquid["kind"]

EventRole = Literal[
    "candidate",
    "finished_witness",
    "auxiliary",
    "preparation",
    "optional",
    "negated",
    "unsupported",
]


@dataclass(frozen=True)
class CookingEvent:
    """Exact source span, canonical method and limited action classification.

    Attributes:
        method: Canonical verb, or empty for an unresolved appliance operation.
        role: Finishing action/completed-body witness, prep or unsupported evidence.
        start: Inclusive character offset in original source steps.
        end: Exclusive character offset; no rewritten source text.
        text: Original substring at the recorded offsets.
    """

    method: str
    role: EventRole
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class CookingMethodEvidence:
    """Last supported non-auxiliary source action and retained cooking exposure.

    Attributes:
        main_methods: At most one supported finishing method; empty is unknown.
        executed_methods: Supported source actions including prep/toppings,
            not a physical execution measurement or oil/nutrition estimate.
        events: Source spans and finite classifications for review.
        reason: Evidence status, not measured cooking or clinical suitability.
    """

    main_methods: tuple[str, ...]
    executed_methods: tuple[str, ...]
    events: tuple[CookingEvent, ...]
    reason: str


@lru_cache(maxsize=8192)
def _from_steps(steps: str) -> CookingMethodEvidence:
    equipment = tuple((m.start(), m.end()) for m in _EQUIPMENT.finditer(steps))
    separators = tuple(m.start() for m in _SEPARATOR.finditer(steps))
    executions = tuple(
        match
        for match in _EXECUTION.finditer(steps)
        if not _NEGATION.search(steps[max(0, match.start() - 160) : match.start()])
    )
    events: list[CookingEvent] = []
    for match in _ACTION.finditer(steps):
        if any(start <= match.start() < end for start, end in equipment):
            continue
        left = max((pos + 1 for pos in separators if pos < match.start()), default=0)
        right = min((pos for pos in separators if pos >= match.end()), default=len(steps))
        segment = steps[left:right]
        prefix, suffix = steps[left : match.start()], steps[match.end() : right]
        active_prefix = prefix
        if not prefix.strip() and left and steps[left - 1] in "，,":
            previous = max((item.end() for item in _SEPARATOR.finditer(steps[:left - 1])), default=0)
            active_prefix = steps[previous:match.start()]
        active_body = bool(_BODY.search(segment) or _BODY.search(active_prefix))
        method = _ALIASES[match.group()]
        # Purchased food names may contain a cooking verb, but unpacking a
        # "steamed dumpling" is not evidence of locally executing steam.
        # A later explicit reheating action is still parsed independently.
        if _PREPARED_PREFIX.search(prefix) and _PREPARED_NOUN.match(suffix):
            continue
        # Already-cooked ingredients and mechanically mixed components do not
        # establish a newly executed finishing operation.
        if re.match(r"(?:好的|过的|熟的|完的|好了的)", suffix) or prefix.endswith("已经"):
            # A source's post-execution "pour over the steamed dish" explicitly
            # identifies its completed body. Initial cooked ingredients, absent
            # execution and negated commands cannot provide this witness.
            if (
                re.search(r"淋(?:在|到)$", prefix)
                and any(command.end() < match.start() for command in executions)
                and re.match(r"(?:好的|熟的)", suffix)
            ):
                events.append(
                    CookingEvent(
                        method, "finished_witness", match.start(), match.end(), match.group()
                    )
                )
            continue
        if method == "拌" and match.start() and steps[match.start() - 1] in "搅抄":
            continue
        role: EventRole = "candidate"
        prior_heating = [
            event
            for event in events
            if event.role in {"candidate", "finished_witness"}
            and event.method in {"蒸", "煮", "炖", "炒", "烤", "煎", "炸", "焖", "烧", "焯"}
        ]
        linked_drained_body = bool(
            _DRAINED_BODY.match(prefix)
            and re.match(r"(?:至)?熟", suffix)
            and prior_heating
            and prior_heating[-1].method in {"焯", "煮"}
            and _DRAINED_PREPARATION.fullmatch(steps[prior_heating[-1].end : left])
        )
        poured_liquid = _poured_liquid_role(steps, left, match.start(), right)
        if _NEGATION.search(prefix):
            role = "negated"
        elif re.match(r"[”\"']?模式", suffix) and (
            "参考" in segment
            or not re.search(r"分钟|℃|°C|启动|开始烹饪", steps[match.end() : match.end() + 48])
        ):
            role = "unsupported"
        elif (
            _OPTIONAL.search(prefix)
            or re.search(r"(?:或|也可|均可|可选)", segment)
            and len(list(_ACTION.finditer(segment))) > 1
        ):
            role = "optional"
        elif method != "拌" and (
            _AUXILIARY.search(segment)
            and not active_body
            and not linked_drained_body
            or re.search(r"(?:做|作|制成|用于|作为)[^。；;]{0,8}浇头", segment)
            or re.search(r"(?:油|锅)[^。；;]{0,6}烧热", segment)
            or (
                method == "烧"
                and re.match(r"的(?:滚烫|热)[的\s]*热?油", suffix)
                and _affirmative_pour(prefix)
            )
            or (
                not active_body
                and _AUXILIARY.search(steps[right : right + 48])
                and _affirmative_pour(steps[right : right + 48])
                and any(
                    event.role in {"candidate", "finished_witness"}
                    and _SERVE.search(steps[event.end : match.start()])
                    for event in events
                )
            )
            or poured_liquid == "油"
            or (
                poured_liquid in {"汁", "水"}
                and _AUXILIARY.search(steps[right:right + 160])
                and any(event.role in {"candidate", "finished_witness"}
                        and _PLATED.search(steps[event.end:match.start()]) for event in events)
            )
        ):
            role = "auxiliary"
        elif (
            method == "拌"
            and match.group() == "拌"
            and _SEASONING_ADDITION.match(suffix)
            and prior_heating
            and _SEASONING_AFTER_HEAT.fullmatch(steps[prior_heating[-1].end : match.start()])
            and not _COLD_MIX.search(steps[prior_heating[-1].end : right])
        ):
            # Adding a declared seasoning after heating does not turn the
            # finished dish into a cold mixed preparation. Keep the earlier
            # heating and this auxiliary exposure; explicit cooling wins.
            role = "auxiliary"
        elif method == "拌" and (
            _UNFINISHED.search(segment) or not _SERVE.search(steps[match.end() : match.end() + 32])
        ):
            role = "preparation"
        elif re.search(r"尚未启动|未启动|没有启动", steps[match.end() : match.end() + 32]):
            role = "unsupported"
        events.append(CookingEvent(method, role, match.start(), match.end(), match.group()))
    for match in _EXECUTION.finditer(steps):
        # Explicitly exclusive steam equipment plus an execution command is
        # usable; a steam/bake rack or unspecified intelligent device is not.
        prior = steps[max(0, match.start() - 160) : match.start()]
        method = "蒸" if _STEAM_DEVICE.search(prior) else ""
        role = "negated" if _NEGATION.search(prior) else "candidate" if method else "unsupported"
        events.append(CookingEvent(method, role, match.start(), match.end(), match.group()))
    # Preserve a known ambiguous source spelling rather than borrowing an
    # earlier preparation boil or silently correcting it to stir-frying.
    for match in re.finditer("抄拌", steps):
        events.append(CookingEvent("", "unsupported", match.start(), match.end(), match.group()))
    events.sort(key=lambda event: event.start)
    candidates = [
        e for e in events if e.role in {"candidate", "finished_witness", "optional", "unsupported"}
    ]
    main = candidates[-1] if candidates else None
    established = bool(main and main.role in {"candidate", "finished_witness"})
    return CookingMethodEvidence(
        (main.method,) if main and established else (),
        tuple(
            dict.fromkeys(
                e.method
                for e in events
                if e.role in {"candidate", "finished_witness", "auxiliary"} and e.method
            )
        ),
        tuple(events),
        (
            "supported_completed_body_witness"
            if main and main.role == "finished_witness"
            else "supported_finishing_action" if established else "finishing_action_not_established"
        ),
    )


def cooking_method_evidence(recipe: Recipe) -> CookingMethodEvidence:
    """Read finite finishing and supporting action evidence from original steps.

    Args:
        recipe: Source recipe. Title and stored method tags never supply proof.

    Returns:
        Last explicit non-auxiliary cooking/finished mixing action, retaining
        earlier heating/topping exposures and exact source spans. A completed
        body witness after local execution may confirm an otherwise unspecified
        machine operation; initial cooked ingredients never supply that proof. Alternatives,
        unresolved final appliance operations and missing evidence stay unknown.
        This is a limited language heuristic, not a physical cooking measurement,
        full procedural parser or assessment of oil amounts/clinical safety.
    """
    return _from_steps(recipe.steps)


def source_cooking_method_evidence(steps: str) -> CookingMethodEvidence:
    """Read source steps before building a normalized recipe.

    Args:
        steps: Complete original preparation text, never inferred method tags.

    Returns:
        The same immutable finite action evidence as the recipe-facing API.
        Source spans remain in the unmodified text; missing evidence is unknown.

    Example:
        >>> source_cooking_method_evidence("食材蒸熟后装盘。").main_methods
        ('蒸',)
        >>> source_cooking_method_evidence("食材放入烤肠模具备用。").main_methods
        ()
    """
    return _from_steps(steps)


def main_cooking_methods(recipe: Recipe) -> tuple[str, ...]:
    """Return established finishing methods without rewarding unknown metadata.

    Args:
        recipe: Original source steps, independent of title/method tag claims.

    Returns:
        Zero or one canonical method under the versioned evidence contract.
        Empty means unknown, not raw food, no cooking or superior diversity.
    """
    return cooking_method_evidence(recipe).main_methods
