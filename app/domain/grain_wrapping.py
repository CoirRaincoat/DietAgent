"""Finite binding of a wrapping action to grain dough, not unrelated foil/meat."""

import re
from dataclasses import dataclass

_FORM = re.compile(
    r"(?:揉|擀|发酵)[^，,。；;]{0,16}面(?:团|皮|饼|片)"
    r"|面(?:团|皮|饼|片)[^，,。；;]{0,16}(?:揉|擀|发酵)"
    r"|搅拌成团"
    r"|(?:剂子|面剂)[^，,。；;]{0,16}擀成"
)
_WRAP = r"(?:包入|包起|包上|包有|包裹|卷起来|卷起|缠绕)"
_DOUGH = r"(?:面团|面皮|面饼|面片|包子皮|皮子)"
_DIRECT = re.compile(
    _DOUGH
    + r"(?:擀平|摊开|平铺|切好|擀好|后|上|中|里|再|将|把|取|用|的|好|成)*"
    + _WRAP
    + r"[^，,。；;]{1,24}"
    r"|[^，,。；;]{1,18}包入(?:擀好的)?面(?:团|皮|饼)"
    r"|面皮包(?:一份|适量)?馅料"
    r"|包入[^，,。；;]{1,18}将包子皮"
    r"|面团(?:将|把)[^，,。；;]{1,12}缠绕"
)
_CONTINUATION = re.compile(
    r"^(?:第\d+步[:：])?(?:将|把)?(?:发酵好后|醒好后|随后|然后|再)?" + _WRAP + r"[^，,。；;]{1,24}"
)
_REVERSE_CONTEXT = re.compile(r"[^，,。；;]{1,18}包入(?:皮中|其中)")
_SHAPING = re.compile(r"^(?:搓圆|压扁|搓圆压扁|擀平|切成|分成|两边薄的皮)")
_OTHER_CONTAINER = re.compile(r"包入(?:豆腐皮|豆皮|荷叶|白菜叶|锡纸|保鲜膜|碗|锅)")
_UNASSERTED = re.compile(
    r"不要|不用|无需|无须|不必|不能|不可|尚未|没有|禁止|勿|并非|不是|不包|未包"
    r"|如果|假如|假设|可以|可选|也可|示例|举例|参考|另一道|可能|建议|[“”‘’\"『』]"
)


@dataclass(frozen=True)
class GrainWrapping:
    """Exact unmodified source slice; geometric role evidence, not quantity or safety."""

    start: int
    end: int
    text: str


def grain_dough_wrapping(steps: str) -> GrainWrapping | None:
    """Require executed dough formation and locally bound outer-grain wrapping.

    Explicit dough subjects or an immediately following subjectless wrapping
    instruction can establish binding. A global mention of flour/dough plus
    'foil wraps the duck' cannot. Unsupported distant pronouns/implicit wrappers
    remain unrecognized; named grain forms have separate caller fallback rules.
    """
    positions = [index for index, char in enumerate(steps) if not char.isspace()]
    text = "".join(steps[index] for index in positions)
    formed = False
    previous_dough = False
    for clause in re.finditer(r"[^，,。；;]+", text):
        value = clause.group()
        asserted = not (_UNASSERTED.search(value) or _OTHER_CONTAINER.search(value))
        current_form = asserted and _FORM.search(value) is not None
        formed = formed or current_form
        direct = _DIRECT.search(value) if asserted and formed else None
        continuation = (
            (_CONTINUATION.search(value) or _REVERSE_CONTEXT.search(value))
            if asserted and previous_dough
            else None
        )
        witness = direct or continuation
        if witness is not None:
            start = positions[clause.start() + witness.start()]
            end = positions[clause.start() + witness.end() - 1] + 1
            return GrainWrapping(start, end, steps[start:end])
        previous_dough = bool(
            asserted
            and formed
            and (
                current_form
                or re.search(_DOUGH, value)
                or (previous_dough and _SHAPING.search(value))
            )
        )
    return None
