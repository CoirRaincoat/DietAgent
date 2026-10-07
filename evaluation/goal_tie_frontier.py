"""Offline Pareto filtering within the same gain/edit-count search stratum."""

from typing import Generic, TypeVar

T = TypeVar("T")


class GoalTieFrontier(Generic[T]):
    """Keep nondominated finite reference vectors, then original stable order.

    The first two rank entries (gain and change count) retain priority. This
    never sums goals or makes an incomparable goal tradeoff on the user's
    behalf. Stability selects among remaining incomparable observations; it
    does not certify optimality outside the finite search or clinical quality.
    """

    def __init__(self) -> None:
        self.primary: tuple[int, int] | None = None
        self.dimension: int | None = None
        self.values: dict[tuple[int, ...], tuple[tuple[int, ...], T]] = {}

    def consider(self, rank: tuple[int, ...], goals: tuple[int, ...], value: T) -> None:
        if len(rank) < 2:
            raise ValueError("rank needs gain and changed-count entries")
        if self.dimension is not None and len(goals) != self.dimension:
            raise ValueError("goal dimensions must be identical")
        self.dimension = len(goals)
        primary = (rank[0], rank[1])
        if self.primary is not None and primary > self.primary:
            return
        if self.primary is None or primary < self.primary:
            self.primary = primary
            self.values.clear()
        for other, (other_rank, _) in self.values.items():
            if other == goals:
                if other_rank <= rank:
                    return
            elif all(a >= b for a, b in zip(other, goals)):
                return
        dominated = [
            other
            for other in self.values
            if other == goals or all(a >= b for a, b in zip(goals, other))
        ]
        for other in dominated:
            del self.values[other]
        self.values[goals] = (rank, value)

    def best(self) -> tuple[tuple[int, ...], T] | None:
        return min(self.values.values(), key=lambda entry: entry[0]) if self.values else None
