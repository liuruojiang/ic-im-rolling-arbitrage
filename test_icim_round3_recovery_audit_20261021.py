"""Independent recovery audit; all future dates/prices are synthetic fixtures.

Uses the real archive reader, public builder, coordinator and temporary StateStore.
No production runtime, original evidence, or provider endpoint is opened.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import ic_im_signal_evidence_v1 as evidence
import ic_im_evidence_adapter_v1 as adapter
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_ordinary_put_route_fix_20261013 as seed


DAY = date(2026, 11, 20)
NEXT = date(2026, 11, 23)
PREVIOUS = date(2026, 11, 19)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("network forbidden"))
    monkeypatch.setattr(bot.fear_grid, "fetch_jiu_reference", lambda *a, **k: {})
    monkeypatch.setattr(bot, "build_iv_warning", lambda *a, **k: {"status": "synthetic_audit_monitor_omitted"})
    monkeypatch.delenv("ICIM_EVIDENCE_DIR", raising=False)


def clock(day, hour=18, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=bot.BEIJING)


def fake_wall(monkeypatch, day, hour=18):
    now = clock(day, hour)
    class SyntheticWall(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(bot, "datetime", SyntheticWall)
    monkeypatch.setattr(state, "datetime", SyntheticWall)
    monkeypatch.setattr(bot.fear_grid, "_validation_clock", lambda: now)
    monkeypatch.setattr(evidence, "_now", lambda: now.astimezone(timezone.utc))
    return now


def prepared_record(previous=PREVIOUS):
    record = seed._record()
    record.update(verified_day=str(previous), sequence=0, previous_digest=None, signals={},
                  source="synthetic_round3_recovery_independent_audit")
    for product, anchor in record["products"].items():
        anchor.update(last_verified_day=str(previous), verified_grid_entry_source="none",
                      verified_next_grid_entry_source="none", post_core_contract=product + "2612")
    record["products"]["IC"].update(
        post_put_contract="510500P2612M07500", post_put_security_id="10012099",
        v14_core_put_contract="510500P2612M07500", v14_core_put_security_id="10012099",
        v14_core_put_qty=14, verified_core_put_qty=14, post_put_qty=14,
        verified_put_qty_normalized=14, verified_core_put_delta=.25,
        verified_momentum_put_delta=0., verified_total_put_delta=.25)
    record["digest"] = state._digest(record)
    state._validate_record(record)
    return record


def temporary_store(path, previous=PREVIOUS):
    record = prepared_record(previous)
    store = state.StateStore(path)
    store._atomic_write(store.journal_dir / f"000000-{previous}.json", record)
    store._atomic_write(store.latest_path, record)
    return store, store.load_latest()


def values(product, day):
    template = json.loads(seed.FIXTURE.read_text(encoding="utf-8"))["signals"][product]
    live = dict(template, history_date=day, price=7800. if product == "IC" else 8000.,
                score=3., momentum_120=-.1, momentum_current_weight=0., momentum_next_weight=0.,
                momentum_signal_date=day, grid_current_units=0., grid_target_units=0.,
                valuation_provenance={"audit": "synthetic_original_snapshot"},
                momentum_volume_placeholder=False, momentum_score=-1., momentum_abs20=-.1,
                momentum_volume_ratio=1., momentum_volume_pass=True, momentum_score_hot=False,
                momentum_base_nav=1., momentum_base_dd=0., momentum_nav_defense=False)
    if product == "IC":
        live.update(v13_put_target_delta=.25, v13_core_put_delta=.25, v13_momentum_put_delta=0.,
                    valuation_tier=0, valuation_tier_label="synthetic", valuation_put_delta=.25,
                    mom120_floor_delta=.25, core_put_driver="synthetic protected core",
                    momentum_put_driver="synthetic empty momentum")
    else:
        live.update(v13_parent_puts_per_full_core=3, v13_current_parent_puts=3,
                    v13_put_target_qty_normalized=1.5, absolute_valuation_tier=0,
                    absolute_valuation_tier_label="synthetic", relative_valuation_tier=0,
                    relative_valuation_tier_label="synthetic", valuation_puts_per_full_core=0,
                    mom120_floor_puts_per_full_core=3, core_put_driver="synthetic negative momentum")
    contracts = sorted(bot._expected_future_contracts(product, day))
    future = pd.DataFrame([dict(instrument=c, lastprice=7800. if product == "IC" else 8000.,
                               bprice=7799. if product == "IC" else 7999.,
                               sprice=7801. if product == "IC" else 8001.,
                               volume=1000., position=2000., source_date=day) for c in contracts])
    future.attrs.update(source="synthetic_original_official_close", source_date=day,
                        listed_instruments=contracts)
    mo = pd.DataFrame([dict(instrument=f"{month}-{kind}-{strike}", lastprice=200., bprice=199.,
                            sprice=201., openprice=200., volume=10., position=20., source_date=day)
                       for month in sorted(bot._expected_mo_months(day))
                       for kind in ["C", "P"] for strike in [7200, 8160, 9000]])
    mo.attrs.update(source="synthetic_original_official_close", source_date=day,
                    listed_instruments=mo.instrument.tolist())
    fear = dict(fear_greed_index=40., fear_data_status="same_day_post_close", fear_data_date=day,
                fear_page_updated_at=day.isoformat() + "T15:01:00+08:00", fear_csv_sha256="a" * 64,
                fear_retrieved_at=day.isoformat() + "T15:30:00+08:00", fear_source_url=bot.fear_grid.CSV_URL)
    # Synthetic listing follows the exchange-month shape, not a claim that
    # the generated contract IDs/prices were observed from an exchange.
    year, month = day.year, day.month
    if day > bot._fourth_wednesday(year, month):
        year, month = bot._month_after(year, month)
    following = bot._month_after(year, month)
    months = [(year, month), following, *bot._following_quarter_months(*following, 2)]
    chains = {}
    for y, m in months:
        month = f"{y:04d}{m:02d}"
        frame = pd.DataFrame([dict(contract="510500P" + month[2:] + "M07500", last=.4,
                                   chg_rate=None, pre_settle=.4, strike=7.5,
                                   security_id="10012099" if month == "202612" else str(10020000 + int(month[-4:])))])
        chains[month[-4:]] = (frame, dict(date=day.strftime("%Y%m%d"), time="150000",
                                        source="synthetic_original_SSE_chain"))
    rate = dict(revision=bot.chinabond.REVISION, mode="frozen_fallback",
                yield_decimal=float(bot.FROZEN[product]["gov10y"]), used_date=bot.DATA_CUTOFF.isoformat(),
                observed_date=None, source=bot.chinabond.SOURCE, reason="synthetic frozen mode")
    return live, future, mo, fear, chains, rate


def archive(monkeypatch, root, product, day, *, omit=None):
    fake_wall(monkeypatch, day)
    live, future, mo, fear, chains, rate = values(product, day)
    entries = [("market.live", {"product": product}, live),
               ("cffex.quotes", {"product": product}, future),
               ("fear.score", {"day": day, "mode": "close"}, fear),
               ("rate.resolve", {"day": day, "fallback": float(bot.FROZEN[product]["gov10y"]),
                                  "anchor_day": bot.DATA_CUTOFF}, rate)]
    if product == "IC":
        entries += [("ic.months", {}, ["20" + month for month in sorted(chains)]),
                    ("ic.etf", {}, dict(date=day.strftime("%Y%m%d"), time="150000", last=7.8, prev_close=7.8))]
        entries += [("ic.chain", {"month": month, "day": day}, value) for month, value in chains.items()]
    else:
        entries += [("cffex.quotes", {"product": "MO"}, mo)]
    metadata = dict(product=product, input_revision=adapter.INPUT_REVISION, original_close=True,
                    capture_kind="synthetic_independent_audit_fixture", build=bot.BUILD_ID)
    with evidence.capture_session(root, day, clock(day, 17, 59), metadata) as session:
        for role, key, value in entries:
            if (role, key.get("month")) == omit:
                continue
            session.call(role, key, lambda value=value: value)
    return session


def test_no_evidence_context_preserves_provider_keyword_signature():
    owner = SimpleNamespace(resolve=lambda market_day, clock, frozen_rate, anchor_day:
                            (market_day, clock, frozen_rate, anchor_day))
    adapter._keyed(SimpleNamespace(), owner, "resolve", "audit.rate",
                   lambda day, clock, fallback, anchor_day: {"day": day})
    assert owner.resolve(market_day=DAY, clock=clock(DAY), frozen_rate=.02, anchor_day=PREVIOUS) == (
        DAY, clock(DAY), .02, PREVIOUS)


def test_same_day_snapshot_cannot_bind_requested_security_to_a_different_contract(tmp_path, monkeypatch):
    archive(monkeypatch, tmp_path, "IC", DAY)
    reader, original_clock = adapter.load_original(bot, tmp_path, "IC", DAY)
    with evidence.replay_session(reader), bot.runtime_clock(original_clock):
        with pytest.raises((RuntimeError, evidence.EvidenceUnavailable), match="identity|身份"):
            bot.fetch_sse_existing_put_historical_quote("510500P2612M07000", "10012099", DAY)


def test_original_closed_at_after_session_never_becomes_original_day_evidence(tmp_path, monkeypatch):
    archive(monkeypatch, tmp_path, "IM", DAY)
    reader = evidence.load_session(tmp_path, DAY)
    reader.closed_at = clock(NEXT)
    monkeypatch.setattr(evidence, "_load_one", lambda *a, **k: reader)
    with pytest.raises(evidence.EvidenceUnavailable, match="acquisition time"):
        adapter.load_original(bot, tmp_path, "IM", DAY)


def test_missing_candidate_month_never_uses_current_chain(tmp_path, monkeypatch):
    archive(monkeypatch, tmp_path / "evidence", "IC", DAY, omit=("ic.chain", "2703"))
    reader, original_clock = adapter.load_original(bot, tmp_path / "evidence", "IC", DAY)
    with evidence.replay_session(reader), bot.runtime_clock(original_clock):
        with pytest.raises(evidence.EvidenceUnavailable, match="ic.chain"):
            bot._fetch_510500_chain_for_day("2703", DAY)


def test_freeze_then_original_monthly_evidence_can_replay_and_advance_two_sessions(tmp_path, monkeypatch):
    store, current = temporary_store(tmp_path / "ledger")
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", state.anchors_from_record(current))
    root = tmp_path / "evidence"
    for day in [DAY, NEXT]:
        for product in ["IC", "IM"]:
            archive(monkeypatch, root, product, day)
    fake_wall(monkeypatch, date(2026, 11, 24))
    monkeypatch.setenv("ICIM_EVIDENCE_DIR", str(root))
    before = current["digest"]
    with bot.historical_replay(DAY):
        diagnostic = bot.build_live_trade_signal("IC", clock(DAY, 15, 20), "close")
    assert diagnostic["diagnostic_only"] is True
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(current, {"IC": diagnostic, "IM": diagnostic})
    assert store.load_latest()["digest"] == before
    # Explicitly synthetic oracle extension: not a claim about real 2027 holidays.
    monkeypatch.setattr(bot, "_OFFICIAL_EXCHANGE_CALENDAR_YEARS", set(bot._OFFICIAL_EXCHANGE_CALENDAR_YEARS) | {2027})
    for day in [DAY, NEXT]:
        bot.install_runtime_anchors(state.anchors_from_record(current))
        with bot.historical_replay(day):
            signals = {product: bot.build_live_trade_signal(product, clock(day, 15, 20), "close")
                       for product in ["IC", "IM"]}
        for product, signal in signals.items():
            assert not signal.get("diagnostic_only"), (product, signal)
            assert signal["signal_complete"] is True and signal["persistence_eligible"] is True
            assert signal["signal_generation_timing"] == "retrospective_replay"
        if day == DAY:
            assert signals["IC"]["option_monthly_reset_due"] is True
            assert signals["IC"]["put_target_contract"].startswith("510500P2703")
            assert signals["IM"]["put_target_contract"].startswith("MO2703")
        current = store.append_confirmed_signals(current, signals)
        assert current["verified_day"] == day.isoformat()
        assert state.StateStore(store.root).load_latest()["digest"] == current["digest"]
    assert current["sequence"] == 2


def coordinator(monkeypatch, path, store, evidence_root):
    monkeypatch.setenv("ICIM_STATE_DIR", str(path / "unused_module_default"))
    monkeypatch.setenv("ICIM_REQUIRE_MIGRATION", "0")
    monkeypatch.setenv("ICIM_DISABLE_INTERNAL_REFRESH", "1")
    monkeypatch.setenv("ICIM_EVIDENCE_DIR", str(evidence_root))
    module = importlib.import_module("poe_ic_im_v1_4_server")
    return module.LedgerCoordinator(store)


def test_real_server_catchup_recovers_monthly_reset_and_continues_next_session(tmp_path, monkeypatch):
    store, initial = temporary_store(tmp_path / "ledger")
    for day in [DAY, NEXT]:
        for product in ["IC", "IM"]:
            archive(monkeypatch, tmp_path / "evidence", product, day)
    now = fake_wall(monkeypatch, date(2026, 11, 24), 8)
    service = coordinator(monkeypatch, tmp_path, store, tmp_path / "evidence")
    assert service.catch_up_until_current(now, max_sessions=4) == 0
    assert store.load_latest()["digest"] == initial["digest"]
    assert "2026-11-20" in service.last_refresh_diagnostic
    monkeypatch.setattr(bot, "_OFFICIAL_EXCHANGE_CALENDAR_YEARS", set(bot._OFFICIAL_EXCHANGE_CALENDAR_YEARS) | {2027})
    assert service.catch_up_until_current(now, max_sessions=4) == 2, service.last_refresh_diagnostic
    latest = store.load_latest()
    assert latest["sequence"] == 2 and latest["verified_day"] == NEXT.isoformat()
    assert service.last_refresh_error is None
    assert state.StateStore(store.root).load_latest()["digest"] == latest["digest"]


def test_continuous_56_session_synthetic_archive_server_recovery(tmp_path, monkeypatch):
    start, end = date(2026, 10, 15), date(2026, 12, 31)
    days = [start + timedelta(days=n) for n in range((end - start).days + 1)
            if bot._is_exchange_trading_day(start + timedelta(days=n))]
    assert len(days) == 56
    store, initial = temporary_store(tmp_path / "ledger", previous=date(2026, 10, 14))
    for day in days:
        for product in ["IC", "IM"]:
            archive(monkeypatch, tmp_path / "evidence", product, day)
    wall = fake_wall(monkeypatch, date(2027, 1, 4), 8)
    service = coordinator(monkeypatch, tmp_path, store, tmp_path / "evidence")
    # With only the real 2026 coverage, genuine ledger advancement stops at
    # the monthly reset that would adopt an unverified 2027 target.
    advanced_before = service.catch_up_until_current(clock(NEXT), max_sessions=64)
    assert advanced_before == days.index(DAY), service.last_refresh_diagnostic
    frozen = store.load_latest()
    assert frozen["verified_day"] == PREVIOUS.isoformat()
    frozen_digest = frozen["digest"]
    assert service.catch_up_until_current(clock(NEXT), max_sessions=64) == 0
    assert store.load_latest()["digest"] == frozen_digest
    # Explicitly synthetic calendar extension, isolated to this test process.
    monkeypatch.setattr(bot, "_OFFICIAL_EXCHANGE_CALENDAR_YEARS", set(bot._OFFICIAL_EXCHANGE_CALENDAR_YEARS) | {2027})
    monkeypatch.setattr(bot, "_EXCHANGE_CLOSURES", bot._EXCHANGE_CLOSURES | {date(2027, 1, 1)})
    advanced_after = service.catch_up_until_current(wall, max_sessions=64)
    assert advanced_before + advanced_after == 56, service.last_refresh_diagnostic
    latest = store.load_latest()
    assert latest["sequence"] == 56 and latest["verified_day"] == end.isoformat()
    assert state.StateStore(store.root).load_latest()["digest"] == latest["digest"]
    assert service.last_refresh_error is None
    for sequence, day in enumerate(days, 1):
        record = store.load_sequence(sequence)
        assert record["verified_day"] == day.isoformat()
        for product, signal in record["signals"].items():
            assert signal["signal_complete"] is True and signal["persistence_eligible"] is True
            assert not signal.get("diagnostic_only") and not signal.get("calendar_provisional")
            assert signal["original_evidence"]["replay_runtime"].startswith(day.isoformat())


@pytest.mark.parametrize("change", [
    {"source": "unverified vendor"},
    {"yield_decimal": True},
    {"yield_decimal": float("nan")},
    {"mode": "invented"},
    {"yield_decimal": .299},
    {"mode": "official_actual", "fetched_at": "2026-11-20T17:29:00+08:00"},
    {"mode": "official_actual", "fetched_at": "2026-11-20T19:00:00+08:00"},
    {"mode": "official_actual", "fetched_at": "2026-11-20T17:45:00", "page_sha256": "a" * 64},
])
def test_archived_rate_revalidates_source_numeric_and_publication_clock(tmp_path, monkeypatch, change):
    archive(monkeypatch, tmp_path, "IM", DAY)
    reader, runtime = adapter.load_original(bot, tmp_path, "IM", DAY)
    rate = values("IM", DAY)[-1]
    if change.get("mode") == "official_actual":
        rate.update(used_date=str(DAY), observed_date=str(DAY), page_sha256="a" * 64)
    rate.update(change)
    key = {"day": DAY, "fallback": float(bot.FROZEN["IM"]["gov10y"]), "anchor_day": bot.DATA_CUTOFF}
    with evidence.replay_session(reader), bot.runtime_clock(runtime):
        with pytest.raises((evidence.EvidenceUnavailable, TypeError, ValueError)):
            adapter._validate_value(bot, "rate.resolve", key, rate)


def test_archived_official_rate_keeps_original_usable_evening_clock(tmp_path, monkeypatch):
    archive(monkeypatch, tmp_path, "IM", DAY)
    reader, runtime = adapter.load_original(bot, tmp_path, "IM", DAY)
    rate = values("IM", DAY)[-1]
    rate.update(mode="official_actual", used_date=str(DAY), observed_date=str(DAY),
                fetched_at="2026-11-20T17:45:00+08:00", page_sha256="a" * 64, yield_decimal=.021)
    key = {"day": DAY, "fallback": float(bot.FROZEN["IM"]["gov10y"]), "anchor_day": bot.DATA_CUTOFF}
    with evidence.replay_session(reader), bot.runtime_clock(runtime):
        assert adapter._validate_value(bot, "rate.resolve", key, rate)["yield_decimal"] == .021
    # The same evidence is unavailable under the old fixed 15:20 replay clock.
    with evidence.replay_session(reader), bot.runtime_clock(clock(DAY, 15, 20)):
        with pytest.raises(evidence.EvidenceUnavailable):
            adapter._validate_value(bot, "rate.resolve", key, rate)


def test_dated_open_supplement_keeps_actual_later_retrieval_and_separate_manifest(tmp_path, monkeypatch):
    archive(monkeypatch, tmp_path, "IC", DAY)
    reader, runtime = adapter.load_original(bot, tmp_path, "IC", DAY)
    actual = fake_wall(monkeypatch, date(2026, 11, 24), 8)
    calls = []
    def dated_provider(security_id, day):
        calls.append((security_id, day))
        return .33, "Sina:dated_open"
    opened = adapter._dated_open_source(bot, dated_provider)
    with evidence.replay_session(reader), bot.runtime_clock(runtime):
        assert opened("10012099", DAY) == (.33, "Sina:dated_open")
        assert opened("10012099", DAY) == (.33, "Sina:dated_open")
    assert calls == [("10012099", DAY)]
    supplement = evidence.load_session(tmp_path, DAY, metadata_filter={"original_close": False})
    assert supplement.closed_at == actual.astimezone(timezone.utc)
    assert supplement.metadata["capture_kind"] == "dated_open_supplement"
    assert reader.supplemental_evidence[-1]["original_close"] is False
    original, _ = adapter.load_original(bot, tmp_path, "IC", DAY)
    assert original.manifest_sha256 == reader.manifest_sha256


@pytest.mark.parametrize("returned", [(.33, "Sina:close"), (False, "Sina:dated_open"), (float("nan"), "Eastmoney:dated_open")])
def test_dated_open_supplement_cannot_substitute_close_or_invalid_price(tmp_path, monkeypatch, returned):
    archive(monkeypatch, tmp_path, "IC", DAY)
    reader, runtime = adapter.load_original(bot, tmp_path, "IC", DAY)
    fake_wall(monkeypatch, date(2026, 11, 24), 8)
    opened = adapter._dated_open_source(bot, lambda *a: returned)
    with evidence.replay_session(reader), bot.runtime_clock(runtime):
        with pytest.raises(evidence.EvidenceUnavailable):
            opened("10012099", DAY)


def independent_capture_strategy(monkeypatch):
    now = fake_wall(monkeypatch, NEXT)
    class CaptureWall(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(adapter, "datetime", CaptureWall)
    samples = {p: values(p, NEXT) for p in ["IC", "IM"]}
    rate = SimpleNamespace(resolve=evidence.source("rate.resolve", lambda day, fallback: {
        "day": day, "fallback": fallback, "anchor_day": bot.DATA_CUTOFF})(lambda day, fallback:
            next(v[-1] for p, v in samples.items() if v[-1]["yield_decimal"] == fallback)))
    fake = SimpleNamespace(BEIJING=bot.BEIJING, BUILD_ID=bot.BUILD_ID, time=bot.time,
        SIGNAL_PRODUCT_NETWORK_BUDGET_SECONDS=45., STRATEGY_QUERY_LOCK=bot.STRATEGY_QUERY_LOCK,
        _require_official_exchange_calendar=bot._require_official_exchange_calendar,
        _is_exchange_trading_day=bot._is_exchange_trading_day, _market_phase=bot._market_phase,
        runtime_clock=bot.runtime_clock, _network_budget=bot._network_budget,
        LIVE_CONTINUATION_ANCHOR={p: {"last_verified_day": PREVIOUS} for p in ["IC", "IM"]})
    fake.live_proxy = evidence.source("market.live", lambda product, clock: {"product": product})(
        lambda product, clock: samples[product][0])
    fake.fetch_cffex_quotes = evidence.source("cffex.quotes", lambda product, clock: {"product": product})(
        lambda product, clock: samples["IM"][2] if product == "MO" else samples[product][1])
    fake.fear_grid = SimpleNamespace(fetch_score=evidence.source("fear.score", lambda day, mode: {"day": day, "mode": mode})(
        lambda day, mode: samples["IM"][3]))
    fake._gov10y_for_day = lambda product, day: rate.resolve(day, float(bot.FROZEN[product]["gov10y"]))["yield_decimal"]
    fake.fetch_sse_510500_quote = evidence.source("ic.etf", lambda: {})(lambda:
        dict(date=NEXT.strftime("%Y%m%d"), time="150000", last=7.8, prev_close=7.8))
    fake.fetch_sse_510500_expiries = evidence.source("ic.months", lambda: {})(lambda:
        ["20" + month for month in sorted(samples["IC"][4])])
    fake._fetch_510500_chain_for_day = evidence.source("ic.chain", lambda month, day: {"month": month, "day": day})(
        lambda month, day: samples["IC"][4][month])
    return fake, now


def test_independent_capture_continues_with_old_ledger_anchor(tmp_path, monkeypatch):
    fake, now = independent_capture_strategy(monkeypatch)
    result = adapter.capture_daily(fake, tmp_path, now)
    assert result["status"] == "captured"
    for product in ["IC", "IM"]:
        reader, _ = adapter.load_original(bot, tmp_path, product, NEXT)
        assert reader.metadata["anchor_day_at_capture"] == PREVIOUS.isoformat()
        assert reader.day == NEXT


def test_wrong_day_etf_capture_never_reports_complete_original_evidence(tmp_path, monkeypatch):
    fake, now = independent_capture_strategy(monkeypatch)
    fake.fetch_sse_510500_quote = lambda: dict(date=DAY.strftime("%Y%m%d"), time="150000", last=7.8, prev_close=7.8)
    adapter._keyed(bot, fake, "fetch_sse_510500_quote", "ic.etf", lambda: {})
    result = adapter.capture_daily(fake, tmp_path, now)
    assert result["status"] == "incomplete"
    assert result["products"]["IC"]["status"] == "incomplete"
    with pytest.raises(evidence.EvidenceUnavailable):
        adapter.load_original(bot, tmp_path, "IC", NEXT)
