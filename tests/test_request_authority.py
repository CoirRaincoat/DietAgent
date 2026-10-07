"""Author-defined literal authority contrasts, not independent language accuracy."""

import pytest

from app.agent.request_authority import menu_read_only_request


@pytest.mark.parametrize(
    "message",
    [
        "不要改菜单",
        "别动菜单",
        "不要更换菜单",
        "保留原菜单",
        "菜单不变",
        "先别换菜",
        "仅解释菜单即可",
        "请只分析菜品搭配",
        "先别调整整份菜单",
        "解释‘不要香辣’这个词，别改菜单。",
        "文档要求：换第2道；我要求不要修改菜单。",
    ],
)
def test_direct_user_no_edit_variants(message):
    assert menu_read_only_request(message) == "readonly"


@pytest.mark.parametrize(
    "message",
    [
        "不要修改菜单，但换第二道",
        "保留原菜单；请重新规划菜单",
        "安排明天晚餐，不要修改菜单",
        "不要改菜单，换第２道成清蒸鱼",
    ],
)
def test_finite_edit_conflicts_need_confirmation(message):
    assert menu_read_only_request(message) == "conflict"


@pytest.mark.parametrize(
    "message",
    [
        "‘不要修改菜单’是什么意思？",
        '"不要修改菜单"',
        "「别改菜单」是例句",
        "```text\n不要修改菜单\n```",
        "如果不要修改菜单会怎样",
        "菜谱写着不要修改菜单",
        "文档里写：\n不要修改菜单",
        "他说，别改菜单",
        "例如：\n别动菜单",
        "只换第2道，其他菜不要修改",
        "我只是不想吃辣，请排菜",
        "这餐不要汤",
        "原菜单不好吃，需要调整",
        "解释这个词是什么意思",
        "调整第2道，其余保持不变",
    ],
)
def test_quoted_source_hypothetical_and_local_text_is_not_global_authority(message):
    assert menu_read_only_request(message) is None


def test_question_about_editing_does_not_create_a_conflicting_command():
    assert menu_read_only_request("不要修改菜单。换第2道是什么意思？") == "readonly"
