"""Synthetic boundary fixtures, not historical or live strategy results."""
import json
from datetime import date, datetime

import pytest

import ic_im_daily_valuation as v
import poe_ic_im_mainline_v1_3_bot as bot


def fixture():
    return {"schema_version": 1, "valuation_date": "2026-09-08",
            "fetched_at": "2026-09-08T16:00:00+08:00", "source_urls": v.SOURCE_URLS,
            "evidence_sha256": "a" * 64, "indices": {
                "000852": {"pe_aggregate_ttm": 30.0, "pb_aggregate": 2.4},
                "000905": {"pe_aggregate_ttm": 27.0, "pb_aggregate": 2.3}}}


def resolve(monkeypatch, raw, day=date(2026, 9, 8), hour=18):
    monkeypatch.setattr(v, "DEFAULT_FILE", __import__("pathlib").Path("/no-such-valuation-file"))
    monkeypatch.delenv(v.FILE_ENV, raising=False)
    monkeypatch.setenv(v.ENV, raw)
    return v.resolve("IM", day, datetime(2026, 9, 8, hour, tzinfo=v.BEIJING),
                     price=8000, frozen=bot.FROZEN["IM"], anchor_day=bot.DATA_CUTOFF)


def test_actual_and_proxy_keep_auxiliary_inputs_explicit(monkeypatch):
    actual = resolve(monkeypatch, json.dumps(fixture()))
    assert actual["pe"] == 30.0 and actual["pb"] == 2.4
    assert actual["provenance"]["mode"] == "vip_actual"
    assert "股息" in v.disclosure(actual["provenance"])
    missing = resolve(monkeypatch, "")
    expected = bot.FROZEN["IM"]["pe"] * 8000 / bot.FROZEN["IM"]["price"]
    assert missing["pe"] == pytest.approx(expected)
    assert "代理估值" in v.disclosure(missing["provenance"])
    assert "2026-08-14" in v.disclosure(missing["provenance"])


@pytest.mark.parametrize("mutation", [
    lambda s: s["indices"].pop("000905"),
    lambda s: s["indices"]["000905"].update(pb_aggregate=0),
    lambda s: s["indices"]["000852"].update(pe_aggregate_ttm=float("nan")),
    lambda s: s["indices"]["000852"].update(pb_aggregate=True),
    lambda s: s.update(cookie="DO_NOT_LEAK"),
    lambda s: s.update(fetched_at="2026-09-08T16:00:00"),
    lambda s: s.update(source_urls=[]),
    lambda s: s.update(evidence_sha256="bad"),
])
def test_invalid_or_partial_snapshot_falls_back_as_pair(monkeypatch, mutation):
    snapshot = fixture()
    mutation(snapshot)
    value = resolve(monkeypatch, json.dumps(snapshot))
    assert value["provenance"]["mode"] == "proxy"
    assert "校验失败" in v.disclosure(value["provenance"])
    assert "DO_NOT_LEAK" not in str(value)


def test_stale_future_intraday_and_malformed_are_disclosed(monkeypatch):
    for day in (date(2026, 9, 7), date(2026, 9, 9)):
        value = resolve(monkeypatch, json.dumps(fixture()), day)
        assert value["provenance"]["mode"] == "proxy"
        assert value["provenance"]["real_data_date"] == "2026-09-08"
    assert resolve(monkeypatch, json.dumps(fixture()), hour=14)["provenance"]["mode"] == "proxy"
    assert resolve(monkeypatch, "bad secret")["provenance"]["mode"] == "proxy"


def test_stored_report_does_not_relabel_proxy_after_late_upload(monkeypatch):
    import run_ic_im_v1_3_github_digest as runner
    monkeypatch.setenv(v.ENV, json.dumps(fixture()))
    report = runner.render_stored_close_report({
        "signals": {"IC": {}, "IM": {}}, "verified_day": "2026-09-08"})
    assert "代理估值" in report
    assert "真实PE/PB：乐咕VIP" not in report


def test_grid_reuses_persisted_state_after_real_day():
    # In-band score must retain yesterday's actual-input-driven grid position.
    live = {"grid_current_units": 1.0, "score": 1.8, "history_date": "2026-09-11"}
    assert bot._daily_grid_target("IM", live) == 1.0
    live["grid_current_units"] = 0.0
    assert bot._daily_grid_target("IM", live) == 0.0


def test_live_calculation_uses_actual_pe_pb_for_score_and_put(monkeypatch):
    from test_poe_ic_im_mainline_v1_3_bot import _full_ic_history, _as_ohlcv
    snapshot = fixture()
    monkeypatch.setenv(v.ENV, json.dumps(snapshot))
    frame = _as_ohlcv(_full_ic_history("2026-09-08"))
    monkeypatch.setattr(bot, "fetch_ohlcv_history", lambda _: frame.copy())
    monkeypatch.setattr(bot, "fetch_live_price_quote", lambda _: {
        "price": float(frame.close.iloc[-1]), "source": "unit-test-fixture",
        "source_date": date(2026, 9, 8), "source_time": "15:00:00"})
    live = bot.live_proxy("IC", datetime(2026, 9, 8, 18, tzinfo=v.BEIJING))
    fields = snapshot["indices"]["000905"]
    expected = bot.valuation_score(fields["pb_aggregate"],
        1 / fields["pe_aggregate_ttm"] - bot.FROZEN["IC"]["gov10y"], bot.FROZEN["IC"]["dividend"])
    assert live["score"] == expected
    assert live["score"] != bot._proxy_score_for_price("IC", live["price"])
    assert live["valuation_put_delta"] == bot.ic_targets(expected, live["momentum_120"])["valuation_put_delta"]
    live["score"] = 1.5
    assert bot._daily_grid_target("IM", live) == 1.0
    live["score"] = 2.1
    assert bot._daily_grid_target("IM", live) == 0.0
