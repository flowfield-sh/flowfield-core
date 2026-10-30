"""Price retained usage, never native invoices or guessed missing counters."""

import copy
from contextlib import closing

import pytest
from usage_fixtures import accounting, event, summary

from flowfield import cost, storage
from flowfield.application import Workspace
from flowfield.cost_models import Cost, PriceContext, PricingEvidence, Valuation
from flowfield.usage import Usage
from flowfield.usage_models import Tokens
from flowfield.usage_reads import UsageReads


def test_codex_estimate_preserves_unknown_counters_and_missing_model():
    from flowfield.adapters.codex_usage import observation

    measured = Tokens(input=21582, output=152, cache_read=13184, cache_write=0, total=21734)
    receipt = observation(
        measured,
        session="session",
        thread="session",
        turn="turn",
        version="0.164.0",
        resumed=False,
        inherited=None,
        pricing=PricingEvidence(model="gpt-6-astra", provider="openai"),
    )
    from flowfield.usage import normalize

    delta, _ = normalize(receipt)
    assert delta.cache_write is None
    result = cost.value(receipt, delta)
    assert result.cost.nanodollars == (8398 * 10 + 13184 + 152 * 50) * 1000
    assert result.cost.estimates == ["cache-writes-at-input-rate"]
    assert delta.cache_write is None
    assert cost.value(receipt.model_copy(update={"pricing": None}), delta).cost.nanodollars is None
    assert cost.value(receipt, delta.model_copy(update={"input": None})).cost.nanodollars is None


def test_published_upgrade_repairs_only_unpriced_codex_receipts(tmp_path):
    execution, usage, (run,) = accounting(tmp_path)
    receipt = event(
        run,
        contract="codex-thread-total/0.164.0",
        semantics="inclusive-cache",
        counters=Tokens(input=100, output=20, cache_read=50, total=120),
        pricing=PricingEvidence(model="gpt-6-astra", provider="openai"),
    )
    usage.observe("harbor", "run", run.id, receipt)
    with closing(storage.connect(execution.workspace.database)) as db:
        db.execute(
            "UPDATE usage_observations SET cost=?",
            (cost.unavailable(120, "token-components-unreported").model_dump_json(),),
        )
        db.execute("PRAGMA user_version=92")
        original = list(db.execute("SELECT data,delta,fingerprint FROM usage_observations"))
    upgraded = Workspace(execution.workspace.directory)
    with upgraded.connection() as db:
        assert (
            list(map(tuple, db.execute("SELECT data,delta,fingerprint FROM usage_observations")))
            == original
        )
        saved = db.execute("SELECT cost FROM usage_observations").fetchone()[0]
        assert Valuation.model_validate_json(saved).cost.nanodollars == 1550000
    # Opening again neither recalculates the estimate nor creates another backup.
    backups = list((upgraded.directory / "backups").iterdir())
    Workspace(upgraded.directory)
    assert list((upgraded.directory / "backups").iterdir()) == backups
    with upgraded.connection() as db:
        cost.repair_codex_costs(db)
        assert db.execute("SELECT cost FROM usage_observations").fetchone()[0] == saved


def context(**changes):
    return PriceContext(model="claude-sonnet-5-5", provider="anthropic", **changes)


def tokens(**changes):
    return Tokens(
        input=160, output=20, cache_read=50, cache_write=10, reasoning=10, total=180
    ).model_copy(update=changes)


def test_disjoint_cache_and_reasoning_subsets_are_not_charged_twice():
    value = cost.price(tokens(), context(cache_write_short=6, cache_write_long=4))
    assert value.cost.nanodollars == 436000  # 100*2 + 20*10 + 50*.1 + 6*2.5 + 4*4
    assert value.cost.priced_tokens == 180
    assert value.cost.rate_dates == ["2026-10-29"]
    assert value.rates[0]["model"] == "claude-sonnet-5-5"
    assert (
        cost.price(tokens(reasoning=None), context(cache_write_short=6, cache_write_long=4))
        == value
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ({}, "cache-retention-unreported"),
        ({"model": "unpriced"}, "price-unavailable"),
        ({"provider": "gateway"}, "price-unavailable"),
        ({"service_tier": "fast"}, "price-tier-unavailable"),
    ],
)
def test_unknown_inputs_do_not_become_zero(change, reason):
    evidence = context().model_copy(update=change)
    value = cost.price(tokens(), evidence)
    assert value.cost.nanodollars is None
    assert value.cost.reasons == [reason]
    assert value.cost.unpriced_tokens == 180


def test_context_tiers_use_requests_not_session_size():
    t = Tokens(input=200000, output=1000, cache_read=0, cache_write=0, total=201000)
    p = PriceContext(model="claude-haiku-5-5", provider="anthropic")
    assert cost.price(t, p).cost.reasons == ["request-context-unreported"]
    short = cost.price(t, p.model_copy(update={"input_observed": 200000, "max_input": 100000}))
    long = cost.price(t, p.model_copy(update={"input_observed": 200000, "max_input": 200000}))
    assert short.cost.nanodollars == 20500000
    assert long.cost.nanodollars == 102500000
    assert cost.price(
        t, p.model_copy(update={"input_observed": 200000, "max_input": 150000})
    ).cost.reasons == ["mixed-context-tiers"]


def test_missing_components_overflow_and_zero():
    assert cost.price(tokens(cache_write=None), context()).cost.nanodollars is None
    assert cost.price(
        tokens(input=9007199254740991, output=9007199254740991),
        context(cache_write_short=10, cache_write_long=0),
    ).cost.reasons == ["cost-overflow"]
    assert cost.price(Tokens(total=0), None).cost.nanodollars == 0
    combined = cost.combine(
        [Cost(nanodollars=5, known_nanodollars=5, coverage="complete"), Cost(unpriced_tokens=10)]
    )
    assert combined.nanodollars is None and combined.known_nanodollars == 5
    assert combined.coverage == "partial"


def test_frozen_catalog_replay_resume_and_restart(tmp_path, monkeypatch):
    execution, usage, (run,) = accounting(tmp_path)
    p = PricingEvidence(**context(cache_write_short=10, cache_write_long=0).model_dump())
    first = event(run, pricing=p)
    usage.observe("harbor", "run", run.id, first)
    before = summary(execution).totals.cost
    assert before.nanodollars == 430000
    replacement = copy.deepcopy(cost.catalog())
    replacement["version"] = "future"
    replacement["verified_on"] = "2027-01-01"
    replacement["models"]["anthropic/claude-sonnet-5-5"]["input"] = "4"
    monkeypatch.setattr(cost, "catalog", lambda: replacement)
    usage = Usage(Workspace(execution.workspace.directory))
    # Supplemental metadata changes cannot turn an old report into new spending.
    usage.observe("harbor", "run", run.id, first.model_copy(update={"pricing": None}))
    assert summary(execution).totals.cost == before
    usage.observe(
        "harbor", "run", run.id, event(run, 200, identity="resumed", pricing=p, baseline="resume")
    )
    total = summary(execution).totals.cost
    assert total.nanodollars == 1060000
    assert total.rate_dates == ["2026-10-29", "2027-01-01"]
    receipts = UsageReads(execution.workspace).evidence("harbor", "run", run.id).items
    assert receipts[0].valuation.rates[0]["rates"]["input"] == "2"
    usage.rebuild()
    assert summary(execution).totals.cost == total


@pytest.mark.parametrize("version", [90, 91])
def test_existing_usage_upgrade_preserves_unpriced_history(tmp_path, version):
    execution, usage, (run,) = accounting(tmp_path)
    usage.observe("harbor", "run", run.id, event(run))
    with closing(storage.connect(execution.workspace.database)) as db:
        db.execute("ALTER TABLE usage_observations DROP COLUMN cost")
        db.execute(f"PRAGMA user_version={version}")
        original = list(db.execute("SELECT data,delta,fingerprint FROM usage_observations"))
    upgraded = Workspace(execution.workspace.directory)
    with upgraded.connection() as db:
        assert [
            tuple(r) for r in db.execute("SELECT data,delta,fingerprint FROM usage_observations")
        ] == original
        assert db.execute("SELECT cost FROM usage_observations").fetchone()[0] is None
    assert list((upgraded.directory / "backups").glob(f"schema-{version}-*.sqlite3"))
    record = UsageReads(upgraded).evidence("harbor", "run", run.id)
    assert record.execution.tokens.total == 180
    assert record.execution.cost.nanodollars is None
    assert record.execution.cost.reasons == ["not-valued"]


def test_each_reported_model_part_is_priced_and_partial_coverage_stays_unknown(tmp_path):
    from flowfield.adapters.pi_usage import pricing_part

    execution, _, (run,) = accounting(tmp_path)
    part = pricing_part(
        {
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "provider": "anthropic",
            "usage": {
                "input": 100,
                "output": 20,
                "cacheRead": 50,
                "cacheWrite": 10,
                "cacheWrite1h": 0,
            },
        }
    )
    assert part is not None
    helper = part.model_copy(update={"model": "claude-haiku-5-5"})
    e = event(run, pricing=PricingEvidence(parts=[part, helper]))
    value = cost.value(e, tokens(input=320, output=40, cache_read=100, cache_write=20, total=360))
    assert value.cost.nanodollars == 451750
    assert len(value.rates) == 2
    assert cost.value(e, tokens()).cost.reasons == ["request-coverage-incomplete"]
    assert pricing_part({"role": "assistant", "usage": {"input": True}}) is None


def test_catalog_validation_and_optional_metadata_isolation(tmp_path):
    cost.validate_catalog(cost.catalog())
    invalid = copy.deepcopy(cost.catalog())
    invalid["models"]["anthropic/claude-sonnet-5-5"]["input"] = "NaN"
    with pytest.raises(ValueError, match="Rates"):
        cost.validate_catalog(invalid)
    execution, usage, (run,) = accounting(tmp_path)
    usage.observe("harbor", "run", run.id, event(run, pricing={"parts": [{"input": -1}]}))
    result = summary(execution)
    assert result.totals.tokens.total == 180
    assert result.totals.cost.nanodollars is None


def test_request_reports_establish_zero_without_defaulting_aggregate_fields(tmp_path):
    from flowfield.cost_models import PricePart

    _, _, (run,) = accounting(tmp_path)
    part = PricePart(
        model="claude-sonnet-5-5",
        provider="anthropic",
        input=100,
        output=20,
        cache_read=0,
        cache_write=0,
    )
    observed = event(run, pricing=PricingEvidence(parts=[part]))
    value = cost.value(observed, Tokens(input=100, output=20, total=120))
    assert value.cost.nanodollars == 400000
    assert cost.value(observed, Tokens(total=121)).cost.nanodollars is None
    assert cost.value(observed, Tokens()).cost.nanodollars is None


def test_terminal_incomplete_consumption_keeps_known_cost_without_claiming_complete(tmp_path):
    execution, usage, (run,) = accounting(tmp_path)
    usage.observe(
        "harbor",
        "run",
        run.id,
        event(
            run,
            final=False,
            gap="native-error",
            pricing=PricingEvidence(
                model="claude-sonnet-5-5",
                provider="anthropic",
                cache_write_short=10,
                cache_write_long=0,
            ),
        ),
    )
    usage.end("harbor", "run", run.id, confirmed=True)
    view = summary(execution)
    assert view.totals.cost.nanodollars is None
    assert view.totals.cost.known_nanodollars == 430000
    assert view.totals.cost.coverage == "partial"
    detail = UsageReads(execution.workspace).evidence("harbor", "run", run.id)
    assert detail.execution.cost == view.totals.cost
    assert detail.items[0].valuation.cost.nanodollars == 430000
