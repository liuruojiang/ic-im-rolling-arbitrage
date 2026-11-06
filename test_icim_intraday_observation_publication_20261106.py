"""Publication regressions; mocked collection never represents new market data."""
import copy
import json
from datetime import datetime

import pytest
import run_ic_im_v1_4_github_digest as runner


def install(monkeypatch, *, degraded=False, missing=False):
    clock = datetime.fromisoformat("2026-11-06T14:30:00+08:00")
    latest = {"verified_day": "2026-11-05", "sequence": 30, "digest": "test-chain"}
    observed = {p: {"product": p, "market_date": "2026-11-06", "state_anchor_day": "2026-11-05",
                    "next_trade_date": "2026-11-09", "future_quote_date": "2026-11-06",
                    "close_confirmed": False, "market_phase": "盘中"} for p in runner.PRODUCTS}
    if missing:
        del observed["IM"]
    else:
        observed["IM"].update(diagnostic_only=True, diagnostic_status="calendar_unverified",
                              diagnostic_reason="缺少2027日历")

    class Store:
        def __init__(self, *_): pass
        def load_latest(self): return copy.deepcopy(latest)
    class Coordinator:
        def __init__(self, *_): pass
        def catch_up_until_current(self, *_a, **_k): return 0
        def health(self, *_):
            return {"status": "degraded" if degraded else "ok", "verified_day": latest["verified_day"],
                    "refresh_error": "旧锚点待续接" if degraded else None}
        def execute_query(self, *_a, **_k):
            return "本次IC报价及IM缺日历观察", [], copy.deepcopy(observed)

    monkeypatch.setattr(runner, "StateStore", Store)
    monkeypatch.setattr(runner, "LedgerCoordinator", Coordinator)
    monkeypatch.setattr(runner.strategy.fear_grid, "publication_view", lambda signal: dict(signal))
    def validate(signal, product, **_):
        if product == "IM":
            raise RuntimeError("缺少2027日历")
    monkeypatch.setattr(runner.state_module, "validate_delivery_values", validate)
    return clock, latest, observed


@pytest.mark.parametrize("degraded,missing", [(False, False), (True, False), (False, True)])
def test_observations_survive_one_product_or_ledger_gap(monkeypatch, tmp_path, degraded, missing):
    clock, latest, _ = install(monkeypatch, degraded=degraded, missing=missing)
    before = copy.deepcopy(latest)
    result = runner.build_artifacts(state_dir=tmp_path / "state", out_dir=tmp_path / "out", clock=clock,
                                    max_sessions=1, mode="realtime", expected_market_date="2026-11-06")
    assert result["status"] == "partial"
    assert result["publication_contract_revision"] == runner.OBSERVATION_REVISION
    assert result["product_observations"]["IC"]["status"] == "verified"
    assert result["product_observations"]["IM"]["status"] == ("unavailable" if missing else "unverified")
    assert "signals" not in result
    for key in ("persistence_eligible", "ledger_recovery_eligible", "complete_portfolio_target", "production_authorized"):
        assert result[key] is False
    assert result["ledger_unchanged"] is True and latest == before
    assert "本次IC报价" in (tmp_path / "out" / result["report_file"]).read_text(encoding="utf-8")
    assert json.loads((tmp_path / "out/result.json").read_text(encoding="utf-8")) == result


def test_observation_cli_returns_partial_not_failed(monkeypatch, tmp_path, capsys):
    clock, _, _ = install(monkeypatch)
    monkeypatch.setattr("sys.argv", ["digest", "--state-dir", str(tmp_path / "state"), "--out-dir", str(tmp_path / "out"),
                                   "--mode", "realtime", "--now", clock.isoformat()])
    assert runner.main() == 0
    assert "ic_im_digest_partial:" in capsys.readouterr().out
    assert not (tmp_path / "out/failure.txt").exists()


def test_changed_ledger_never_qualifies_observation_targets(monkeypatch, tmp_path):
    clock, latest, observed = install(monkeypatch)
    changed = {**latest, "digest": "changed"}
    result = runner._observation_artifact(out_dir=tmp_path, clock=clock, completed_day=clock.date().replace(day=5),
        before=latest, after=changed, observed=observed, report="本次观察", reason="账本变化")
    assert result["ledger_unchanged"] is False
    assert all(item["status"] == "unverified" for item in result["product_observations"].values())


def test_close_mode_retains_original_strict_health_boundary(monkeypatch, tmp_path):
    clock, _, _ = install(monkeypatch, degraded=True)
    with pytest.raises(runner._ArtifactBuildError):
        runner.build_artifacts(state_dir=tmp_path / "state", out_dir=tmp_path, clock=clock,
                               max_sessions=1, mode="close")


def test_missing_fear_cannot_qualify_an_internal_retained_grid(monkeypatch, tmp_path):
    clock, latest, observed = install(monkeypatch)
    def unavailable(signal):
        return {**signal, "grid_target_confirmed": False, "total_units_target": None, "grid_target": None}
    monkeypatch.setattr(runner.strategy.fear_grid, "publication_view", unavailable)
    result = runner._observation_artifact(out_dir=tmp_path, clock=clock, completed_day=clock.date().replace(day=5),
        before=latest, after=latest, observed=observed, report="其他资料继续展示", reason="恐慌缺值")
    assert result["product_observations"]["IC"]["status"] == "unverified"
    assert result["product_observations"]["IC"]["signal"]["total_units_target"] is None


def test_bad_product_date_does_not_hide_other_observations(monkeypatch, tmp_path):
    clock, latest, observed = install(monkeypatch)
    observed["IM"]["market_date"] = "bad-date"
    def strict_view(signal):
        if signal["market_date"] == "bad-date":
            raise ValueError("品种日期非法")
        return dict(signal)
    monkeypatch.setattr(runner.strategy.fear_grid, "publication_view", strict_view)
    result = runner._observation_artifact(out_dir=tmp_path, clock=clock, completed_day=clock.date().replace(day=5),
        before=latest, after=latest, observed=observed, report="IC有效资料仍展示", reason="IM日期异常")
    assert result["product_observations"]["IC"]["status"] == "verified"
    assert result["product_observations"]["IM"]["status"] == "unverified"


def test_unknown_next_session_is_a_field_warning_not_empty_report(monkeypatch, tmp_path):
    clock, latest, observed = install(monkeypatch)
    def absent(*_a, **_k): raise runner.strategy.CalendarCoverageError("下一交易日日历缺失")
    monkeypatch.setattr(runner.strategy, "_roll_forward_exchange_day", absent)
    result = runner._observation_artifact(out_dir=tmp_path, clock=clock, completed_day=clock.date().replace(day=5),
        before=latest, after=latest, observed=observed, report="已取到的信息保留", reason="日历缺失")
    assert result["next_trade_day"] is None and result["calendar_warning"]
    assert result["product_observations"]["IC"]["signal"] is not None
    assert result["product_observations"]["IC"]["status"] == "unverified"


@pytest.mark.parametrize("field,value", [("next_trade_date", "2026-11-10"), ("future_quote_date", "2026-11-05")])
def test_date_mismatch_is_reported_without_qualifying_target(monkeypatch, tmp_path, field, value):
    clock, latest, observed = install(monkeypatch)
    observed["IC"][field] = value
    result = runner._observation_artifact(out_dir=tmp_path, clock=clock, completed_day=clock.date().replace(day=5),
        before=latest, after=latest, observed=observed, report="已取得信息继续展示", reason="日期待确认")
    assert result["product_observations"]["IC"]["status"] == "unverified"
    assert result["product_observations"]["IC"]["signal"][field] == value


def test_real_builder_and_state_partial_reaches_observation_publication(monkeypatch, tmp_path):
    from test_icim_new_put_calendar_partial_20261106 import wire_new_momentum, INTRADAY_CLOCK
    wire_new_momentum(monkeypatch, intraday=True)
    monkeypatch.setattr(runner.strategy.requests, "get", lambda *_a, **_k: pytest.fail("offline fixture only"))
    signal = runner.strategy.build_live_trade_signal("IM", INTRADAY_CLOCK, "intraday")
    assert signal["calendar_partial_revision"]
    before = {"verified_day": "2026-11-05", "sequence": 30, "digest": "isolated-fixture-chain"}
    result = runner._observation_artifact(out_dir=tmp_path, clock=INTRADAY_CLOCK,
        completed_day=INTRADAY_CLOCK.date().replace(day=5), before=before, after=copy.deepcopy(before),
        observed={"IM": signal}, report="isolated native builder fixture", reason="具体Put待确认")
    row = result["product_observations"]["IM"]
    assert row["status"] == "partial", row["validation_error"]
    assert row["signal"]["momentum_units_target"] == .25
    assert row["signal"]["momentum_put_required_qty_normalized"] == .75
    assert row["signal"]["momentum_put_target_contract"] is None
    assert row["signal"]["momentum_put_target_qty_normalized"] is None
    assert result["persistence_eligible"] is False
    # AUTO consumer coverage uses a tracked canonical-JSON native-output fixture
    # in that repository, so this strategy regression remains single-checkout.
    saved = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert saved == result
