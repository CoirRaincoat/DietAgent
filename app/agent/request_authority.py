"""Finite user no-edit clauses, independent of extracted model action fields.

This grants no safety override, context confirmation, recipe labels or generic
natural-language understanding. Quoted/source examples are not user authority.
"""

import re
from typing import Literal

VERSION = "literal-menu-read-only-v1"
_QUOTED = re.compile(r"```[\s\S]*?```|\"[^\"]*\"|'[^']*'|“[^”]*”|‘[^’]*’|「[^」]*」")
_READ_ONLY = re.compile(
    r"(?:请)?(?:先|暂时|这次|本轮)?(?:不要|别|不用)(?:再)?"
    r"(?:修改|更改|改变|调整|重排|重新安排|重新规划|更换|换|改|动)"
    r"(?:这份|整份|当前|原来|原|本餐|这餐|本轮)?(?:菜单|菜)"
    r"|(?:请)?(?:先)?(?:只|仅)(?:解释|说明|分析|评价|评估)(?:一下)?"
    r"(?:(?:这份|当前|原|本餐|这餐)?菜单|菜品搭配)?(?:即可|就好)?"
    r"|(?:这份|当前|原)?菜单(?:保持|保留)(?:原样|不变)"
    r"|(?:这份|当前|原)?菜单不变"
    r"|(?:保留|保持)(?:这份|当前|原)?菜单(?:不变|原样)?"
)
_EDIT = re.compile(
    r"(?:请|帮我)?(?:只|仅)?(?:换|替换|更换)(?:掉)?(?:第)?"
    r"[一二三四五六七八九十0-9０-９]+(?:道|个)(?:菜)?(?:.*)"
    r"|(?:请|帮我)?(?:重新|再)?(?:规划|安排|推荐|重排)"
    r"(?:一份|下一餐|新一餐|明天晚餐|这餐|本餐)?(?:菜单)"
    r"|(?:请|帮我)?(?:重新)?(?:安排|推荐|规划)(?:下一餐|新一餐|明天晚餐)"
)
_DESCRIPTIVE = re.compile(r"吗|[?？]|是什么意思|如果|假如|例如|比如|解释|说明|引用")
_SOURCE_HEAD = re.compile(
    r"(?:他说|她说|例如|比如|(?:文档|菜谱|报告|日志|样例|示例|原文|模型)(?:里|中)?(?:写着|写了|写|说|要求|内容)?)[：:]?"
)
_USER_HEAD = re.compile(r"^(?:我要求|我的要求是|本轮要求是)[：:]?")


def empty_continuation_request(message: str) -> bool:
    """A complete direct retry/continue has no authority to amend any facts."""
    return bool(
        re.fullmatch(
            r"(?:继续|按刚才的要求继续|继续按刚才的要求|就这样|再试一次|重试)[。.!！]?",
            message.strip(),
        )
    )


def menu_read_only_request(message: str) -> Literal["readonly", "conflict"] | None:
    """Complete unquoted clauses only, not a substring command extractor.

    A whole-menu no-edit clause conflicting with a finite affirmative edit
    needs clarification, not silently granted local/global editing scope.
    '其他菜不要修改' does not freeze the separately authorized target slot.
    """
    visible = _QUOTED.sub("[引用内容]", message)
    clauses = [re.sub(r"\s+", "", c).strip() for c in re.split(r"[，,。；;！!\r\n]+", visible)]
    # Source headers own following bare clauses; an explicit user requirement
    # prefix ends that attribution. The model cannot turn it into authority.
    user_clauses = []
    source_context = False
    for clause in clauses:
        if _USER_HEAD.match(clause):
            source_context = False
            clause = _USER_HEAD.sub("", clause)
        elif _SOURCE_HEAD.fullmatch(clause):
            source_context = True
            continue
        if not source_context:
            user_clauses.append(clause)
    clauses = user_clauses
    if not any(_READ_ONLY.fullmatch(c) for c in clauses):
        return None
    if any(
        _EDIT.fullmatch(re.sub(r"^(?:但是|但|不过)", "", c)) and not _DESCRIPTIVE.search(c)
        for c in clauses
    ):
        return "conflict"
    return "readonly"
