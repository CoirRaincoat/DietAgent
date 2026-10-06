"""Public canonical-demand boundaries, not independent held-out quality labels."""

import pytest

from app.domain.models import Constraints
from app.rules.engine import RuleEngine
from evaluation.canonical_preference_guard import canonical_preferences, observe_preferences
from evaluation.pair_variety_probe import probe_pair_variety
from tests.test_source_reference_coverage import trio


@pytest.mark.parametrize("value", ["清淡", "家常", "做法：蒸", "做法多样"])
def test_opt_in_accepts_only_finite_canonical_demands(value: str) -> None:
    constraints = Constraints(dish_count=2, preferences=[value])
    pool = trio()
    pool[0].labels = ["清淡", "家常"]
    pool[0].raw_label = "清淡、家常"
    default = probe_pair_variety(pool[:2], pool, constraints, RuleEngine())
    result = probe_pair_variety(
        pool[:2], pool, constraints, RuleEngine(), preference_policy="canonical_source_coverage"
    )
    assert default.status == "unsupported_request_kept_unchanged"
    assert result.recipes == [pool[0], pool[2]]
    assert (
        result.exchanges[0]["explicit_preferences_before"]
        == result.exchanges[0]["explicit_preferences_after"]
    )


@pytest.mark.parametrize(
    "value",
    [
        "不要酸",
        "不要家常",
        "做法：不要蒸",
        "清淡但不要盐",
        "清淡和便当",
        "为什么要蒸？",
        "场景：未知",
        "做法：微波",
        "尽量清淡",
        "",
    ],
)
def test_unknown_negative_mixed_and_noncanonical_are_not_silently_ignored(value: str) -> None:
    pool = trio()
    constraints = Constraints(dish_count=2, preferences=[value])
    assert canonical_preferences(constraints) is None
    result = probe_pair_variety(
        pool[:2], pool, constraints, RuleEngine(), preference_policy="canonical_source_coverage"
    )
    assert result.status == "unsupported_request_kept_unchanged"
    assert result.recipes == pool[:2]


@pytest.mark.parametrize(
    "preferences,people,no_spicy",
    [(["一人食"], 5, False), (["一人食", "聚餐"], 1, False), (["辣"], 1, True)],
)
def test_conflicting_canonical_items_are_refused(
    preferences: list[str], people: int, no_spicy: bool
) -> None:
    assert (
        canonical_preferences(
            Constraints(preferences=preferences, people=people, no_spicy=no_spicy)
        )
        is None
    )


@pytest.mark.parametrize("kind", ["flavor", "scene"])
def test_per_slot_reference_sum_cannot_hide_loss_of_one_requested_type(kind: str) -> None:
    pool = trio()
    values = ["酸", "清淡"] if kind == "flavor" else ["家常", "便当"]
    # Both peers earn one per-slot reference; only the old covers the second
    # request. Relevance equality alone must not erase that reference type.
    pool[0].raw_label = values[0]
    pool[0].labels = [values[0]]
    pool[1].raw_label = values[1]
    pool[1].labels = [values[1]]
    pool[2].raw_label = values[0]
    pool[2].labels = [values[0]]
    result = probe_pair_variety(
        pool[:2],
        pool,
        Constraints(dish_count=2, preferences=values),
        RuleEngine(),
        preference_policy="canonical_source_coverage",
    )
    assert result.recipes == pool[:2]
    assert result.blockers["explicit_source_preference_coverage_lost"] > 0


def test_source_label_flavor_strength_cannot_be_replaced_by_weaker_title() -> None:
    pool = trio()
    pool[1].raw_label = "蒜香"
    pool[1].labels = ["蒜香"]
    pool[2].name = "蒜香米饭"
    constraints = Constraints(dish_count=2, preferences=["蒜香"])
    requests = canonical_preferences(constraints)
    assert requests is not None
    assert observe_preferences([pool[1]], requests) == {"flavor:蒜香": 3}
    assert observe_preferences([pool[2]], requests) == {"flavor:蒜香": 2}
    result = probe_pair_variety(
        pool[:2], pool, constraints, RuleEngine(), preference_policy="canonical_source_coverage"
    )
    assert result.recipes == pool[:2]
    assert result.blockers["explicit_source_preference_coverage_lost"] > 0


def test_missing_source_reference_stays_zero_not_claimed_as_fulfilled() -> None:
    requests = canonical_preferences(Constraints(preferences=["蒜香", "便当", "做法：炒"]))
    assert requests is not None
    assert observe_preferences(trio(), requests) == {
        "flavor:蒜香": 0,
        "scene:便当": 0,
        "method:炒": 0,
    }


@pytest.mark.parametrize("unsafe", ["辣椒", "花生"])
def test_canonical_preferences_do_not_relax_hard_gate(unsafe: str) -> None:
    from app.domain.models import Ingredient

    pool = trio()
    pool[0].raw_label = "家常"
    pool[0].labels = ["家常"]
    pool[2].raw_ingredients += "、" + unsafe
    pool[2].ingredients.append(Ingredient(name=unsafe, raw=unsafe))
    result = probe_pair_variety(
        pool[:2],
        pool,
        Constraints(dish_count=2, preferences=["家常"], no_spicy=True, allergies=["花生"]),
        RuleEngine(),
        preference_policy="canonical_source_coverage",
    )
    assert result.recipes == pool[:2]
