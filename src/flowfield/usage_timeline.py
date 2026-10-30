"""Bounded chart partitions, derived from exact receipts and execution intervals.

Model partitions describe the frozen selected model, not inferred provider usage.
Detailed reported-model accounting remains in UsageReads.breakdowns.
"""

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime

from flowfield.cost import combine
from flowfield.cost_models import Cost
from flowfield.usage import add_tokens
from flowfield.usage_models import Tokens, UsageBucket, UsageSeries, UsageSeriesPoint

Interval = tuple[datetime, datetime]
MAX_SERIES = 8


def merged(intervals: list[Interval]) -> list[Interval]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    result = []
    left, right = ordered[0]
    for start, end in ordered[1:]:
        if start > right:
            result.append((left, right))
            left, right = start, end
        else:
            right = max(right, end)
    return [*result, (left, right)]


def elapsed(intervals: list[Interval]) -> float | None:
    """Parallel running intervals count once; gaps contribute no time."""
    return (
        sum((end - start).total_seconds() for start, end in merged(intervals))
        if intervals
        else None
    )


class Timeline:
    def __init__(self, buckets: list[UsageBucket]):
        self.bounds = [
            (datetime.fromisoformat(b.start), datetime.fromisoformat(b.end)) for b in buckets
        ]
        self.starts = [left for left, _ in self.bounds]
        self.tokens: dict[str | None, dict[int, list[Tokens]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.costs: dict[str | None, dict[int, list[Cost]]] = defaultdict(lambda: defaultdict(list))
        self.intervals: dict[str | None, list[Interval]] = defaultdict(list)

    def receipt(
        self, key: str | None, at: datetime, tokens: Tokens, cost: Cost | None = None
    ) -> None:
        index = bisect_right(self.starts, at) - 1
        if index >= 0 and at < self.bounds[index][1]:
            self.tokens[key][index].append(tokens)
            self.costs[key][index].append(cost or Cost())

    def interval(self, key: str | None, start: datetime, end: datetime) -> None:
        self.intervals[key].append((start, end))

    def points(self, keys: list[str | None]) -> list[UsageSeriesPoint]:
        intervals = merged([interval for key in keys for interval in self.intervals[key]])
        ends = [end for _, end in intervals]
        receipts: dict[int, list[Tokens]] = defaultdict(list)
        costs: dict[int, list[Cost]] = defaultdict(list)
        for key in keys:
            for index, values in self.tokens[key].items():
                receipts[index].extend(values)
                costs[index].extend(self.costs[key][index])
        result = []
        for index, (left, right) in enumerate(self.bounds):
            position = bisect_right(ends, left)
            seconds = None
            while position < len(intervals) and intervals[position][0] < right:
                start, end = intervals[position]
                seconds = (seconds or 0.0) + (min(end, right) - max(start, left)).total_seconds()
                position += 1
            result.append(
                UsageSeriesPoint(
                    tokens=add_tokens(receipts[index]),
                    cost=combine(costs[index]),
                    elapsed_seconds=seconds,
                )
            )
        return result

    def series(self) -> list[UsageSeries]:
        keys = sorted(
            self.tokens.keys() | self.intervals.keys(),
            key=lambda key: (
                -(
                    add_tokens([t for values in self.tokens[key].values() for t in values]).total
                    or 0
                ),
                key or "",
            ),
        )
        shown = keys if len(keys) <= MAX_SERIES else keys[: MAX_SERIES - 1]
        result = [UsageSeries(key=key, points=self.points([key])) for key in shown]
        if len(shown) < len(keys):
            result.append(UsageSeries(key=None, other=True, points=self.points(keys[len(shown) :])))
        return result

    def totals(self) -> list[UsageSeriesPoint]:
        return self.points(list(self.tokens.keys() | self.intervals.keys()))
