"""Known meal context must come from explicit input, never numeric defaults."""

import pytest

from app.agent.clarification import (
    confirm_from_intent,
    missing_questions,
    recover_explicit_meal_context,
)
from app.domain.models import Intent, SessionState


def state(**values):
    return SessionState(session_id="a" * 32, user_id=1, **values)


def test_default_constraints_are_not_confirmed_meal_context():
    value = state()
    assert value.constraints.people == 1
    assert [question.field for question in missing_questions(value)] == [
        "people", "meal_type", "restrictions",
    ]


def test_partial_answers_accumulate_without_reasking_confirmed_fields():
    value = state()
    confirm_from_intent(value, Intent(people=2), "两个人")
    assert [question.field for question in missing_questions(value)] == ["meal_type", "restrictions"]
    confirm_from_intent(value, Intent(meal_type="午餐"), "午餐")
    assert [question.field for question in missing_questions(value)] == ["restrictions"]
    confirm_from_intent(value, Intent(restrictions_confirmed=True), "没有其他忌口")
    assert missing_questions(value) == []


@pytest.mark.parametrize("message", ["随便", "推荐一下", "你决定", "没有", "不是没有忌口", "别按档案", "都能吃吗？"])
def test_vague_answer_cannot_confirm_unknown_restrictions(message):
    value = state()
    confirm_from_intent(value, Intent(restrictions_confirmed=True), message)
    assert "restrictions" not in value.confirmed_fields


def test_short_no_answer_only_resolves_a_single_pending_restriction_question():
    value = state(confirmed_fields=["people", "meal_type"], pending_clarification="有忌口吗？", pending_fields=["restrictions"])
    confirm_from_intent(value, Intent(restrictions_confirmed=True), "没有")
    assert "restrictions" in value.confirmed_fields


def test_no_additional_restrictions_never_erases_allergies():
    value = state()
    value.constraints.allergies = ["海鲜"]
    confirm_from_intent(value, Intent(restrictions_confirmed=True), "按档案忌口，无其他限制")
    assert value.constraints.allergies == ["海鲜"]
    assert "restrictions" in value.confirmed_fields


def test_legacy_serialized_state_reopens_unknown_context():
    raw = state().model_dump()
    raw.pop("confirmed_fields")
    restored = SessionState.model_validate(raw)
    assert len(missing_questions(restored)) == 3


def test_no_answer_to_time_question_does_not_confirm_restrictions():
    value = state(confirmed_fields=["people", "meal_type"], pending_fields=["time_limit"])
    confirm_from_intent(value, Intent(restrictions_confirmed=True), "没有")
    assert "restrictions" not in value.confirmed_fields


@pytest.mark.parametrize("message", [
    "一个人想吃辣，一个人一点辣都不想碰",
    "一个人吃素，另一个人正常吃",
    "其中一人对花生过敏",
    "有一个人不吃海鲜",
    "另一个人想吃清淡",
    "有一个人不喝酒",
    "某个人不想吃辣",
    "一个老人，一个小孩",
])
def test_per_member_description_is_not_recovered_as_party_size(message):
    """Per-member attribute/distribution words are not the party total."""
    assert recover_explicit_meal_context(Intent(), message).people is None


@pytest.mark.parametrize("message", [
    "今天就一个人吃饭",
    "我一个人吃",
    "只有我一个人",
])
def test_lone_person_assertion_still_recovers_single_party(message):
    """An explicit 'alone' assertion is still a one-person party."""
    assert recover_explicit_meal_context(Intent(), message).people == 1


@pytest.mark.parametrize(("message", "expected"), [
    ("我们一共两个人", 2),
    ("两个人吃饭", 2),
    ("总共3个人", 3),
    ("今天四个人吃", 4),
    ("我们有5个人", 5),
    ("1人晚餐", 1),
])
def test_explicit_total_party_size_is_recovered(message, expected):
    """Unambiguous total counts keep recovering, including Arabic '1人'."""
    assert recover_explicit_meal_context(Intent(), message).people == expected
