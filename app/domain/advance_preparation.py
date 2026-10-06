"""Finite source prep notices, never ranking, exclusion or storage advice."""
import re
from collections.abc import Sequence
from functools import lru_cache

from app.domain.models import Recipe

_QUOTES = re.compile(r"“[^”]*”|「[^」]*」|‘[^’]*’|\"[^\"]*\"|'[^']*'")
VERSION = "source-preparation-stages-v2"
_NON_REQUIRED = re.compile(r"不要|不用|无需|不必|不需要|勿|不得|不再|不(?:浸泡|泡发|发酵|醒发|冷藏|静置|松弛)")
_QUALIFIED = re.compile(r"例如|比如|参考|示例|可以|可选|可保存|也可|如果|假如|若(?!干)")
_PREP = re.compile(r"吐沙|浸泡|泡发|发酵|醒发|冷藏|静置|松弛")
_AHEAD = re.compile(r"提前(?:[一二三四五六七八九十两\d]+(?:天|晚|夜|小时)|一晚上|一天晚上)")
_DURATION = r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百两半]+)\s*(?:小时|分钟|天|晚|夜)"
_TIME = re.compile(_DURATION)
_TIME_CONTINUATION = re.compile(r"^(?:持续|等待|约|大约|至少|不少于)?\s*" + _DURATION)
_OTHER_STAGE = re.compile(r"蒸|烤|煮|煎|炒|炸|炖|焖|煲|加热")
_STORAGE = re.compile(r"保存|储存|保质|存放|储藏")


def is_advance_preparation_permission(value: str) -> bool:
    """Exact permission only, not a cooking method or storage-safety claim.

    A quoted/negative/composite/unknown statement is deliberately not folded
    into this permission. The stored preference is never rewritten.
    """
    return re.sub(r"\s+", "", value).replace(":", "：") in {
        "可提前准备", "可以提前准备", "做法：可提前准备", "做法：可以提前准备",
    }


def advance_permission_copy(preferences: Sequence[str]) -> str | None:
    if not any(is_advance_preparation_permission(value) for value in preferences):
        return None
    return "已记录可提前准备；这是备餐安排，不是蒸、炒等成菜做法。仍须核对原步骤与设备；未核总耗时、携带／冷藏保存或再加热条件，不承诺隔夜安全。"


def _action_has_time(clause: str) -> bool:
    action = _PREP.search(clause)
    if action is None:
        return False
    # Do not borrow a baking/steaming time from another action in this clause.
    tail = clause[action.end():]
    if following := _OTHER_STAGE.search(tail):
        tail = tail[:following.start()]
    return bool(_AHEAD.search(clause) or _TIME.search(tail))


@lru_cache(maxsize=8192)
def _source_clauses(steps: str) -> tuple[str, ...]:
    clauses = []
    # Keep comma relationships and their literal punctuation. Optional or
    # conditional context applies forward within a sentence, not backwards
    # to an already asserted prep step. Strong separators reset its scope.
    for sentence in re.split(r"[。；;\r\n]+", _QUOTES.sub("", steps)):
        sentence = re.sub(r"^(?:第\d+步[：:]\s*)?(?:\d+[.．、]\s*)?", "", sentence.strip())
        pieces = list(re.finditer(r"[^，,]+", sentence))
        qualified = False
        negated_actions: set[str] = set()
        blocked = []
        for piece in pieces:
            text = piece.group().strip()
            qualified |= bool(_QUALIFIED.search(text))
            negative = bool(_NON_REQUIRED.search(text))
            if negative:
                negated_actions.update(_PREP.findall(text))
            blocked.append(qualified or negative or bool(_STORAGE.search(text))
                           or bool(negated_actions.intersection(_PREP.findall(text))))
        consumed = -1
        for index, piece in enumerate(pieces):
            text = piece.group().strip()
            if index <= consumed or blocked[index] or not _PREP.search(text):
                continue
            timed = _action_has_time(text)
            last = index
            if index + 1 < len(pieces) and not blocked[index + 1]:
                next_text = pieces[index + 1].group().strip()
                if timed and _PREP.search(next_text) and not _TIME.search(next_text) and not _OTHER_STAGE.search(next_text):
                    last += 1
                elif not timed and _TIME_CONTINUATION.search(next_text) and not _OTHER_STAGE.search(next_text):
                    timed = True
                    last += 1
            if timed:
                clauses.append(sentence[piece.start():pieces[last].end()].strip())
                consumed = last
    return tuple(dict.fromkeys(clauses))


def advance_preparation_copy(chosen: Sequence[Recipe]) -> str | None:
    """Quote only explicit affirmative prep clauses, with no duration estimates.

    Missing notices do not prove quick preparation. Quoted, optional and
    negated clauses cannot become mandatory advance-prep instructions.
    """
    notices = []
    for recipe in chosen:
        clauses = _source_clauses(recipe.steps)
        if clauses:
            notices.append(f"{recipe.name}原方写“" + "；".join(dict.fromkeys(clauses)) + "”。")
    if not notices:
        return None
    return "准备提示：" + "".join(notices) + "请核对原步骤；未核总备餐耗时或保存条件。"
