"""Numeric pricing evidence and immutable USD valuations; no native billing totals."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

Count = Annotated[StrictInt, Field(ge=0, le=9_007_199_254_740_991)]


class PriceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class PriceContext(PriceRecord):
    model: str | None = Field(default=None, max_length=200)
    provider: str | None = Field(default=None, max_length=100)
    cache_write_short: Count | None = None
    cache_write_long: Count | None = None
    input_observed: Count | None = None
    max_input: Count | None = None
    service_tier: Literal["standard", "fast", "flex", "unknown"] = "standard"


class PricePart(PriceContext):
    # All token components are disjoint in supplemental request evidence.
    input: Count
    output: Count
    cache_read: Count
    cache_write: Count


class PricingEvidence(PriceContext):
    parts: list[PricePart] = Field(default_factory=list, max_length=256)


class Cost(PriceRecord):
    nanodollars: Count | None = None
    known_nanodollars: Count | None = None
    coverage: Literal["complete", "partial", "unavailable"] = "unavailable"
    priced_tokens: Count = 0
    unpriced_tokens: Count = 0
    rate_dates: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    estimates: list[str] = Field(default_factory=list)


class Valuation(PriceRecord):
    cost: Cost = Field(default_factory=Cost)
    catalog: str | None = None
    # Retain the exact rows used, not just a pointer to a mutable catalog.
    rates: list[dict[str, object]] = Field(default_factory=list)
