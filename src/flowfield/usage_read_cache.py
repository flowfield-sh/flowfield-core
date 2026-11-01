"""Bounded, disposable calculation reuse. Current SQLite content always supplies the key."""

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Any

from flowfield.cost_models import Cost
from flowfield.usage_models import Tokens, UsageExecution


@dataclass(frozen=True, slots=True)
class Receipt:
    model: str | None
    received_at: datetime
    accepted: bool
    delta: Tokens
    cost: Cost


@dataclass(frozen=True, slots=True)
class Prepared:
    execution: UsageExecution
    receipts: tuple[Receipt, ...]


class UsageReadCache:
    """One service owner; content changes invalidate reuse without write-side bookkeeping.

    Values are internal read-only calculations, never returned directly to callers.
    Budget estimated decoded objects and retained text, and bound
    both entry and observation counts. Restart simply starts cold.
    """

    def __init__(self, *, max_bytes: int = 128 * 1024 * 1024, max_receipts: int = 100_000):
        self.max_bytes, self.max_receipts = max_bytes, max_receipts
        self.bytes = self.receipts = 0
        self._generation = 0
        self._entries: OrderedDict[tuple[str, int], tuple[bytes, int, Prepared, int]] = (
            OrderedDict()
        )
        self._lock = RLock()

    def begin(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def prepare(
        self, row: Any, observations: Sequence[Any], build: Callable[[], Prepared], generation: int
    ) -> Prepared:
        digest = hashlib.sha256()
        # Include full current rows: edits to valuations, dates, coverage and owner
        # state cannot reuse a stale result, even after a restore or row-ID reuse.
        for item in (row, *observations):
            encoded = json.dumps(dict(item), ensure_ascii=False, separators=(",", ":")).encode()
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        signature = digest.digest()
        key = (row["workspace_id"], row["number"])
        with self._lock:
            previous = self._entries.get(key)
            if previous and previous[0] == signature:
                self._entries[key] = (*previous[:3], generation)
                self._entries.move_to_end(key)
                return previous[2]
            # Serialize cold preparation to share work across simultaneous readers.
            value = build()
            # Raw receipt JSON is hashed, not retained. Budget decoded objects
            # plus their variable text, rather than charging discarded input.
            size = 4096 + len(value.execution.model_dump_json().encode())
            size += sum(
                1536 + len(r.cost.model_dump_json().encode()) + len((r.model or "").encode())
                for r in value.receipts
            )
            if previous:
                self._remove(key)
            if size <= self.max_bytes and len(observations) <= self.max_receipts:
                while self._entries and (
                    self.bytes + size > self.max_bytes
                    or self.receipts + len(observations) > self.max_receipts
                    or len(self._entries) >= 10_000
                ):
                    # A scan larger than the budget must not evict the entries
                    # it just reused. Keep a useful subset on every subsequent
                    # read instead of missing on the entire history forever.
                    if next(iter(self._entries.values()))[3] == generation:
                        return value
                    self._remove(next(iter(self._entries)))
                self._entries[key] = signature, size, value, generation
                self.bytes += size
                self.receipts += len(observations)
            return value

    def _remove(self, key: tuple[str, int]) -> None:
        _, size, value, _ = self._entries.pop(key)
        self.bytes -= size
        self.receipts -= len(value.receipts)
