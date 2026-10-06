"""Exact declared alias only, not prose diagnosis or a new health score."""
import pytest

from app.domain.models import Constraints, Diner, DinerUpdate, Intent, UserProfile
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog, dish


@pytest.mark.parametrize("kind", [Constraints, Intent, DinerUpdate, Diner, UserProfile])
def test_exact_declared_daily_heart_goal_is_shared_by_existing_consumers(kind):
    required = {
        DinerUpdate: {"diner": "用户"}, Diner: {"diner_id": "owner", "display_name": "用户"},
        UserProfile: {"user_id": 900001, "age": 30, "sex": "男", "data_scope": "synthetic"},
    }
    obj = kind(health_goals=["日常护心"], **required.get(kind, {}))
    assert obj.health_goals == ["护心"]
    assert kind.model_validate(obj.model_dump()).health_goals == ["护心"]


@pytest.mark.parametrize("text", ["不是日常护心", "日常护心吗", "想咨询日常护心", "日常护心高效治疗", "补钙"])
def test_whole_name_only_unknown_and_negated_prose_stay_unknown(text):
    assert Intent(health_goals=[text]).health_goals == [text]


def test_no_health_goal_inferred_from_other_request_fields():
    intent = Intent(preferences=["日常护心"], query_terms=["日常护心"])
    assert intent.health_goals == []


def test_alias_uses_exact_same_existing_source_evidence_not_new_health_rules():
    source = catalog()[307]
    rules = RuleEngine()
    alias = rules.evaluate(source, Constraints(health_goals=["日常护心"]))
    original = rules.evaluate(source, Constraints(health_goals=["护心"]))
    assert alias == original
    assert not any("暂未配置" in warning for warning in alias.warnings)


def test_goal_never_overrides_allergy_vegan_or_non_spicy():
    source = catalog()[307]
    for changes in ({"allergies": ["鱼"]}, {"diet_mode": "vegan"}):
        assert not RuleEngine().evaluate(source, Constraints(health_goals=["日常护心"], **changes)).allowed
    spicy = dish("蒸豆腐", "豆腐200克；辣椒10克", "豆腐和辣椒蒸熟装盘。")
    assert not RuleEngine().evaluate(spicy, Constraints(health_goals=["日常护心"], no_spicy=True)).allowed
