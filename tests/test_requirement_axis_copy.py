"""Already-stored hard exclusions and prep permission are not taste/method gaps."""
import pytest

from app.agent.menu_balance import analyze_menu_balance
from app.agent.response_copy import required_fact_ids, response_facts
from app.domain.advance_preparation import advance_permission_copy
from app.domain.matching_tags import flavor_preference_issues, supported_flavor_preferences
from app.domain.method_preferences import method_preference_warnings, method_requests
from app.domain.models import Constraints, Intent
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog


def test_recorded_literal_garlic_exclusion_is_not_an_unknown_flavor():
    assert flavor_preference_issues(["不放蒜"], excluded_ingredients=["蒜"]) == ()
    assert supported_flavor_preferences(["不放蒜"]) == ()


@pytest.mark.parametrize("preference,excluded", [
    ("不放蒜", []), ("不放蒜香酱", ["蒜"]), ("少放蒜", ["蒜"]),
    ("不是不放蒜", ["蒜"]), ("不放蒜的做法", ["蒜"]),
])
def test_unrecorded_or_non_exact_food_requirement_not_silently_dismissed(preference, excluded):
    assert flavor_preference_issues([preference], excluded_ingredients=excluded)


def test_negative_taste_and_unknown_flavor_remain_disclosed_alongside_a_food_exclusion():
    issues = flavor_preference_issues(["不放蒜", "不要酸", "咖喱味"], excluded_ingredients=["蒜"])
    assert any("不要酸" in warning for warning in issues)
    assert any("咖喱味" in warning for warning in issues)
    assert not any("尚未核验：不放蒜" in warning for warning in issues)


@pytest.mark.parametrize("preference", ["做法：可提前准备", "做法:可以提前准备", "可提前准备", "可以提前准备"])
def test_exact_advance_permission_is_not_a_cooking_method_or_flavor(preference):
    requests = method_requests([preference])
    assert requests.positive == requests.negative == requests.unknown == ()
    assert not requests.diversity and not requests.negative_diversity
    assert not method_preference_warnings([catalog()[566]], [preference])
    assert not flavor_preference_issues([preference])


@pytest.mark.parametrize("preference", ["做法：分子料理", "做法：不要提前准备", "做法：提前准备过夜安全", "做法：可提前准备也要蒸"])
def test_unknown_or_negative_composite_method_request_is_not_granted_permission(preference):
    assert method_requests([preference]).unknown
    assert method_preference_warnings([catalog()[566]], [preference])


def test_named_method_is_still_required_with_advance_permission():
    assert method_requests(["做法：可提前准备", "做法：蒸"]).positive == ("蒸",)
    assert any("来源参考尚缺：蒸" in warning for warning in method_preference_warnings([catalog()[566]], ["做法：可提前准备", "做法：蒸"]))


@pytest.mark.parametrize("preference", ["不要提前准备", "如果可以提前准备", "可提前准备吗", "‘可提前准备’", "做法：可提前准备过夜安全"])
def test_nonasserted_negative_or_safety_statement_has_no_advance_permission_copy(preference):
    assert advance_permission_copy([preference]) is None


def test_advance_permission_is_a_required_fact_not_a_storage_or_method_certificate():
    menu = [catalog()[566], catalog()[299]]
    constraints = Constraints(preferences=["做法：可提前准备"])
    before = constraints.model_dump()
    facts = response_facts(intent=Intent(), constraints=constraints, diners=[], previous=[],
        chosen=menu, balance=analyze_menu_balance(menu))
    assert "advance_preparation" in required_fact_ids(Intent(), facts)
    assert "浸泡30分钟" in facts["advance_preparation"]
    assert "不是蒸、炒等成菜做法" in facts["advance_preparation"]
    assert "不承诺隔夜安全" in facts["advance_preparation"]
    assert "method_preferences" not in facts
    assert constraints.model_dump() == before


def test_exclusion_disclosure_does_not_remove_hard_requirements_or_clarification():
    rules = RuleEngine()
    known = Constraints(excluded_ingredients=["蒜"], preferences=["不放蒜"])
    source = catalog()[566]
    assert not rules.evaluate(source, known).allowed
    unknown = Constraints(excluded_ingredients=["未收录料汁"], preferences=["不放未收录料汁"])
    assert rules.unresolved_exclusions(unknown) == ["未收录料汁"]
    assert not rules.evaluate(source, unknown).allowed
