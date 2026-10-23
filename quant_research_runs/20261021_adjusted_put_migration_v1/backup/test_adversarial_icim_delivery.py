"""Offline adversarial delivery tests; all ledger writes are temporary."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import os
import tempfile

import pytest
from hypothesis import given, settings, strategies as st

_IMPORT_STATE = tempfile.TemporaryDirectory(prefix="icim_adversarial_import_")
_PREVIOUS_IMPORT_ENV = {key: os.environ.get(key) for key in ("ICIM_STATE_DIR", "ICIM_REQUIRE_MIGRATION")}
os.environ["ICIM_STATE_DIR"] = _IMPORT_STATE.name
os.environ["ICIM_REQUIRE_MIGRATION"] = "0"
try:
    import run_ic_im_v1_3_github_digest as runner
    import poe_ic_im_v1_3_state as state
    from test_poe_ic_im_v1_3_state import _signals
finally:
    for _key, _value in _PREVIOUS_IMPORT_ENV.items():
        if _value is None:
            os.environ.pop(_key, None)
        else:
            os.environ[_key] = _value


@pytest.fixture(autouse=True)
def isolated_test_environment(monkeypatch):
    monkeypatch.setenv("ICIM_STATE_DIR", _IMPORT_STATE.name)
    monkeypatch.setenv("ICIM_REQUIRE_MIGRATION", "0")


@pytest.mark.parametrize("bad", ["false", "true", 1, [True]])
def test_truthy_non_boolean_cannot_commit_ledger(tmp_path, bad):
    store = state.StateStore(tmp_path)
    before = store.initialize()
    signals = _signals(date(2026, 8, 25))
    signals["IM"]["close_confirmed"] = bad
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(before, signals)
    assert store.load_latest()["digest"] == before["digest"]
    assert len(list(store.journal_dir.glob("*.json"))) == 1


@pytest.mark.parametrize("field", ["total_units_current", "total_units_target"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_report_exposure_cannot_commit(tmp_path, field, bad):
    store = state.StateStore(tmp_path)
    before = store.initialize()
    signals = _signals(date(2026, 8, 25))
    signals["IM"][field] = bad
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(before, signals)
    assert store.load_latest()["digest"] == before["digest"]


def test_invalid_realtime_phase_rejected_before_coordinator_or_state_write(tmp_path, monkeypatch):
    calls = []
    class Coordinator:
        def __init__(self, store):
            calls.append("initialize")
            raise RuntimeError("coordinator must not run")
    monkeypatch.setattr(runner, "LedgerCoordinator", Coordinator)
    with pytest.raises(RuntimeError, match="连续交易时段"):
        runner.build_artifacts(state_dir=tmp_path / "state", out_dir=tmp_path / "out",
            clock=datetime(2026, 9, 4, 18, tzinfo=runner.strategy.BEIJING),
            max_sessions=20, mode="realtime")
    assert calls == []
    assert not (tmp_path / "state").exists()


def test_wrong_expected_date_rejected_before_state_initialization(tmp_path, monkeypatch):
    calls = []
    class Coordinator:
        def __init__(self, store):
            calls.append("initialize")
            raise RuntimeError("coordinator must not run")
    monkeypatch.setattr(runner, "LedgerCoordinator", Coordinator)
    with pytest.raises(RuntimeError, match="日报日期不匹配"):
        runner.build_artifacts(state_dir=tmp_path / "state", out_dir=tmp_path / "out",
            clock=datetime(2026, 9, 4, 18, tzinfo=runner.strategy.BEIJING),
            max_sessions=20, expected_market_date="2026-09-03")
    assert calls == []


@pytest.mark.parametrize("product,field,bad", [
    ("IM", "momentum_put_target_qty_normalized", 0.75),
    ("IM", "core_put_current_contract", "MO2612-P-9999"),
    ("IM", "momentum_put_target_contract", None),
    ("IC", "call_target_qty_normalized", -1),
    ("IC", "next_trade_date", date(2026, 8, 27)),
    ("IM", "state_anchor_day", date(2026, 8, 23)),
])
def test_mutated_leg_never_partially_advances(tmp_path, product, field, bad):
    store = state.StateStore(tmp_path)
    before = store.initialize()
    signals = _signals(date(2026, 8, 25))
    signals[product][field] = bad
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(before, signals)
    assert store.load_latest() == before
    assert len(list(store.journal_dir.glob("*.json"))) == 1


@given(st.integers(min_value=1, max_value=500))
@settings(max_examples=30, deadline=None)
def test_arbitrary_digest_changes_fail_closed(delta):
    record = state.bootstrap_record()
    record["products"]["IC"]["verified_put_qty_normalized"] += delta
    with pytest.raises(RuntimeError, match="SHA-256"):
        state._validate_record(record)


@pytest.mark.parametrize("raw,expected", [
    ("2026-09-04T23:00:00+00:00", date(2026, 9, 5)),
    ("2026-09-04T14:30:00", date(2026, 9, 4)),
])
def test_clock_normalizes_beijing_day(raw, expected):
    assert runner.parse_clock(raw).date() == expected


@pytest.mark.parametrize("day", [date(2026, 9, 5), date(2026, 9, 6)])
def test_weekend_completed_anchor_is_friday(day):
    clock = datetime.combine(day, datetime.min.time(), tzinfo=runner.strategy.BEIJING)
    assert runner.strategy._latest_completed_exchange_day(clock) == date(2026, 9, 4)


def test_failure_output_replaces_old_success_without_signals(tmp_path):
    (tmp_path / "result.json").write_text('{"status":"ok","signals":{"IC":"old"}}')
    (tmp_path / "ic_im_v1_3_close_signal.md").write_text("old target")
    runner.write_failure(tmp_path, runner.parse_clock("2026-09-04T18:00:00"), RuntimeError("offline"))
    import json
    failed = json.loads((tmp_path / "result.json").read_text())
    assert failed["status"] == "failed" and "signals" not in failed
    assert not (tmp_path / "ic_im_v1_3_close_signal.md").exists()


@pytest.fixture
def real_close_records():
    """Unmodified accepted run 33850626309, September 4; no live access."""
    root = Path(__file__).parent / "tests" / "fixtures" / "icim_adversarial"
    return [json.loads((root / name).read_text(encoding="utf-8"))
            for name in ("previous.json", "confirmed.json")]


def test_real_confirmed_cloud_record_derives_exact_anchor(real_close_records):
    previous, confirmed = real_close_records
    assert state._jsonable(state.derive_next_anchors(previous, confirmed["signals"])) == confirmed["products"]
    runner.validate_close_artifact(completed_day=date(2026, 9, 4),
        latest=confirmed, observed=confirmed["signals"])


@pytest.mark.parametrize("bad", [None, "false", 0, float("nan")])
def test_real_realtime_missing_or_forged_confirmation_rejected(real_close_records, bad):
    previous, confirmed = real_close_records
    observed = deepcopy(confirmed["signals"])
    for signal in observed.values():
        signal.update(close_confirmed=False, market_phase="盘中")
    observed["IM"]["close_confirmed"] = bad
    with pytest.raises(RuntimeError):
        runner.validate_realtime_artifact(clock=runner.parse_clock("2026-09-04T14:30:00"),
            completed_day=date(2026, 9, 3), before=previous, after=previous, observed=observed)


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "0.75", True, -0.5])
def test_real_realtime_bad_exposure_rejected(real_close_records, bad):
    previous, confirmed = real_close_records
    observed = deepcopy(confirmed["signals"])
    for signal in observed.values():
        signal.update(close_confirmed=False, market_phase="盘中")
    observed["IM"]["total_units_target"] = bad
    with pytest.raises(RuntimeError):
        runner.validate_realtime_artifact(clock=runner.parse_clock("2026-09-04T14:30:00"),
            completed_day=date(2026, 9, 3), before=previous, after=previous, observed=observed)


def test_real_orphan_journal_recovers_original_signal_not_second_quote(tmp_path, monkeypatch, real_close_records):
    previous, confirmed = real_close_records
    store = state.StateStore(tmp_path)
    store._atomic_write(store.journal_dir / "000000-2026-09-03.json", previous)
    store._atomic_write(store.latest_path, previous)
    original = store._atomic_write
    def crash(path, record):
        if path == store.latest_path:
            raise OSError("injected crash after journal durability")
        original(path, record)
    monkeypatch.setattr(store, "_atomic_write", crash)
    with pytest.raises(OSError, match="injected crash"):
        store.append_confirmed_signals(previous, confirmed["signals"])
    assert store.load_latest()["digest"] == previous["digest"]
    monkeypatch.setattr(store, "_atomic_write", original)
    newer_quote = deepcopy(confirmed["signals"])
    newer_quote["IM"]["data_notes"].append("second source response")
    recovered = store.append_confirmed_signals(previous, newer_quote)
    assert recovered["signals"] == confirmed["signals"]
    assert store.load_latest()["digest"] == recovered["digest"]


@pytest.mark.parametrize("error_type,retries", [(runner.strategy.requests.Timeout, 2),
    (runner.strategy.requests.ConnectionError, 2), (RuntimeError, 1)])
def test_transport_retry_count_is_bounded_and_validation_is_not_retried(monkeypatch, error_type, retries):
    calls = []
    def failing(product, mode):
        calls.append((product, mode))
        raise error_type("injected failure")
    monkeypatch.setattr(runner.strategy, "build_live_trade_signal", failing)
    with pytest.raises(error_type):
        runner.strategy.build_live_signal_with_transport_retry("IC", "intraday", 0.5)
    assert calls == [("IC", "intraday")] * retries
