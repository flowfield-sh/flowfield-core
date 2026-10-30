"""Price accepted deltas once. Catalog updates never revalue retained history."""

import json
import re
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from flowfield.cost_models import Cost, PriceContext, Valuation
from flowfield.usage_models import Consumption, Tokens

MAX = 9_007_199_254_740_991


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    value: dict[str, Any] = json.loads(Path(__file__).with_name("prices.json").read_text())
    validate_catalog(value)
    return value


def validate_catalog(value: dict[str, Any]) -> None:
    """Reject ambiguous catalog edits before they can value retained receipts."""
    date.fromisoformat(value["verified_on"])
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", value["version"]):
        raise ValueError("Price catalog version must be YYYY-MM-DD.revision")
    fields = {"input", "output", "cache_read", "cache_short", "cache_long"}
    if not value["models"]:
        raise ValueError("Price catalog has no models")
    for identity, row in value["models"].items():
        provider, model = identity.split("/", 1)
        if not model or not value["sources"][provider].startswith("https://"):
            raise ValueError("Price rows require an exact model and HTTPS source")
        if set(row) - fields - {"context_threshold", "long"}:
            raise ValueError("Unknown rate fields")
        if "context_threshold" in row and (
            type(row["context_threshold"]) is not int or row["context_threshold"] <= 0
        ):
            raise ValueError("Invalid context threshold")
        for rates in (row, row["long"]) if "long" in row else (row,):
            for field in fields:
                raw = rates[field]
                if not isinstance(raw, str) or not re.fullmatch(r"\d+(?:\.\d{1,3})?", raw):
                    raise ValueError(
                        "Rates must be nonnegative decimal strings with nanodollar precision"
                    )


def unavailable(tokens: int | None, reason: str) -> Valuation:
    return Valuation(cost=Cost(unpriced_tokens=tokens or 0, reasons=[reason]))


def combine(values: list[Cost]) -> Cost:
    if not values:
        return Cost()
    known = [v.known_nanodollars for v in values if v.known_nanodollars is not None]
    amount = sum(known) if known else None
    priced = sum(v.priced_tokens for v in values)
    unpriced = sum(v.unpriced_tokens for v in values)
    dates = sorted({d for v in values for d in v.rate_dates})
    reasons = sorted({r for v in values for r in v.reasons})
    if any(n is not None and n > MAX for n in (amount, priced, unpriced)):
        return Cost(reasons=["cost-overflow"], rate_dates=dates)
    complete = all(v.coverage == "complete" for v in values)
    return Cost(
        nanodollars=amount if complete else None,
        known_nanodollars=amount,
        coverage="complete" if complete else "partial" if known else "unavailable",
        priced_tokens=priced,
        unpriced_tokens=unpriced,
        rate_dates=dates,
        reasons=reasons,
    )


def retained(value: str | None, tokens: Tokens) -> Cost:
    return (
        Valuation.model_validate_json(value).cost
        if value
        else unavailable(tokens.total, "not-valued").cost
    )


def price(tokens: Tokens, context: PriceContext | None) -> Valuation:
    if tokens.total == 0:
        return Valuation(cost=Cost(nanodollars=0, known_nanodollars=0, coverage="complete"))
    if context is None or not context.model:
        return unavailable(tokens.total, "model-unreported")
    provider = {"firstParty": "anthropic", "openai-codex": "openai"}.get(
        context.provider or "", context.provider
    )
    model = context.model
    # Dated first-party Claude identifiers have the same published row. Provider
    # aliases and gateways must supply their canonical identity; never fuzzy-match.
    if provider == "anthropic":
        model = re.sub(r"-\d{8}$", "", model)
    prices = catalog()
    row = prices["models"].get(f"{provider}/{model}")
    if not row:
        return unavailable(tokens.total, "price-unavailable")
    if context.service_tier != "standard":
        return unavailable(tokens.total, "price-tier-unavailable")
    input_, output, read, write = tokens.input, tokens.output, tokens.cache_read, tokens.cache_write
    if any(n is None for n in (input_, output, read, write)):
        return unavailable(tokens.total, "token-components-unreported")
    assert input_ is not None and output is not None and read is not None and write is not None
    if read + write > input_:
        return unavailable(tokens.total, "inconsistent-cache-components")
    short, long = context.cache_write_short, context.cache_write_long
    if write == 0:
        short = long = 0
    if short is None or long is None or short + long != write:
        return unavailable(tokens.total, "cache-retention-unreported")
    rates = row
    if threshold := row.get("context_threshold"):
        # A whole-delta upper bound below the threshold proves every request short.
        # Larger aggregate counts never establish that any individual request is long.
        if input_ > threshold:
            if context.input_observed != input_ or context.max_input is None:
                return unavailable(tokens.total, "request-context-unreported")
            if context.max_input > threshold:
                # Mixed request tiers require individual parts, not a single max.
                if context.max_input != input_:
                    return unavailable(tokens.total, "mixed-context-tiers")
                if "long" not in row:
                    return unavailable(tokens.total, "price-tier-unavailable")
                rates = row["long"]
    # USD / million tokens -> nanodollars / token, with decimal arithmetic.
    amount = sum(
        (
            Decimal(str(rates[field])) * count * 1000
            for field, count in (
                ("input", input_ - read - write),
                ("output", output),
                ("cache_read", read),
                ("cache_short", short),
                ("cache_long", long),
            )
        ),
        Decimal(0),
    )
    if amount != amount.to_integral_value() or amount > MAX:
        return unavailable(tokens.total, "cost-overflow")
    return Valuation(
        cost=Cost(
            nanodollars=int(amount),
            known_nanodollars=int(amount),
            coverage="complete",
            priced_tokens=tokens.total or 0,
            rate_dates=[prices["verified_on"]],
        ),
        catalog=prices["version"],
        rates=[
            {
                "provider": provider,
                "model": model,
                "source": prices["sources"][provider],
                "verified_on": prices["verified_on"],
                "tier_source": prices.get("tier_sources", {}).get(f"{provider}/{model}"),
                "rates": rates,
            }
        ],
    )


def value(event: Consumption, delta: Tokens) -> Valuation:
    evidence = event.pricing
    if evidence and evidence.parts:
        parts = [
            Tokens(
                input=p.input + p.cache_read + p.cache_write,
                output=p.output,
                cache_read=p.cache_read,
                cache_write=p.cache_write,
                total=p.input + p.output + p.cache_read + p.cache_write,
            )
            for p in evidence.parts
        ]
        # Explicit request counters can establish zeros that session statistics
        # defaulted. Reconcile the full total and every known aggregate component.
        if delta.total is None or any(
            getattr(delta, key) is not None
            and sum(getattr(p, key) or 0 for p in parts) != getattr(delta, key)
            for key in ("input", "output", "cache_read", "cache_write", "total")
        ):
            return unavailable(delta.total, "request-coverage-incomplete")
        valuations = [price(t, p) for t, p in zip(parts, evidence.parts, strict=True)]
        return Valuation(
            cost=combine([v.cost for v in valuations]),
            catalog=catalog()["version"],
            rates=list(
                {json.dumps(r, sort_keys=True): r for v in valuations for r in v.rates}.values()
            ),
        )
    return price(delta, evidence)
