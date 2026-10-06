"""Same-code frontier experiment; production stays capped until net benefit."""

import pytest

from app.agent import planner as planner_module
from app.agent.planner import MenuPlanner
from app.domain.models import Constraints
from app.rules.engine import RuleEngine
from tests.test_negative_flavor import recipe


def pool():
    chicken = [
        recipe(f"清蒸鸡肉款{index}", foods="鸡胸肉200克；盐1克", steps="鸡胸肉蒸熟装盘。")
        for index in range(70)
    ]
    pork = recipe("清蒸猪肉", foods="猪肉200克；盐1克", steps="猪肉蒸熟装盘。")
    return chicken, pork


@pytest.mark.parametrize("frontier,distinct", [(64, False), (10000, True)])
def test_late_peer_with_distinct_source_food_reaches_similarity_comparison(
    monkeypatch, frontier, distinct
):
    monkeypatch.setattr(planner_module, "_DIVERSITY_POOL_LIMIT", frontier)
    chicken, pork = pool()
    planned = MenuPlanner(RuleEngine()).plan([*chicken, pork], Constraints(dish_count=2))
    assert planned.failure is None
    assert planned.recipes[0] == chicken[0] and (pork in planned.recipes) == distinct


@pytest.mark.parametrize("frontier", [64, 10000])
def test_explicit_food_relevance_still_precedes_diversity(monkeypatch, frontier):
    monkeypatch.setattr(planner_module, "_DIVERSITY_POOL_LIMIT", frontier)
    chicken, pork = pool()
    planned = MenuPlanner(RuleEngine()).plan(
        [*chicken, pork], Constraints(dish_count=2, preferred_ingredients=["鸡肉"])
    )
    assert planned.failure is None and pork not in planned.recipes


@pytest.mark.parametrize("frontier", [64, 10000])
def test_late_peer_cannot_relax_allergy_and_no_spicy(monkeypatch, frontier):
    monkeypatch.setattr(planner_module, "_DIVERSITY_POOL_LIMIT", frontier)
    chicken, pork = pool()
    unsafe = recipe(
        "花生辣椒蒸猪肉", foods="猪肉200克；花生油2克；辣椒1克；盐1克", steps="猪肉与辣椒蒸熟装盘。"
    )
    planned = MenuPlanner(RuleEngine()).plan(
        [*chicken, unsafe, pork], Constraints(dish_count=2, no_spicy=True, allergies=["花生"])
    )
    assert planned.failure is None and unsafe not in planned.recipes


@pytest.mark.parametrize("frontier,distinct", [(64, False), (10000, True)])
def test_late_peer_local_change_preserves_the_other_accepted_slot(monkeypatch, frontier, distinct):
    monkeypatch.setattr(planner_module, "_DIVERSITY_POOL_LIMIT", frontier)
    chicken, pork = pool()
    planned = MenuPlanner(RuleEngine()).plan(
        [*chicken, pork], Constraints(dish_count=2), current=chicken[:2], replace_slot=2
    )
    assert planned.failure is None and planned.recipes[0] == chicken[0]
    assert (planned.recipes[1] == pork) == distinct
