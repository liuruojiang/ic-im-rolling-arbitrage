"""Adversarial delivery regressions; synthetic fault injection, no network/write ledger."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
import threading

import pytest

import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_server as server
import run_ic_im_v1_4_github_digest as digest


class Capture:
    def __init__(self):
        self.parts = []

    @contextmanager
    def start_message(self):
        yield self

    def write(self, value):
        self.parts.append(str(value))

    @property
    def text(self):
        return "".join(self.parts)


@pytest.mark.parametrize("product,route,core", [
    ("IC", "future", .5), ("IM", "future", .5),
    ("IC", "recovery_future", .5), ("IM", "recovery_future", .5),
    ("IC", "short_put", 0), ("IM", "short_put", 0),
    ("IC", "assigned_etf", 0), ("IM", "cash_wait", 0),
])
def test_fallback_snapshot_does_not_invent_core_futures(monkeypatch, product, route, core):
    anchor = deepcopy(bot.LIVE_CONTINUATION_ANCHOR[product])
    anchor.update(policy.default_extension(product))
    anchor.update(v14_route_state=route, verified_momentum_weight=1,
                  verified_grid_units=.5)
    if route == "short_put":
        anchor.update(v14_short_put_contract="510500P2611M07500" if product == "IC" else "MO2611-P-7000",
                      v14_short_put_qty_normalized=10 if product == "IC" else 1.5,
                      v14_short_put_entry_premium=.1,
                      v14_short_put_security_id="10012099" if product == "IC" else None)
    policy.validate_extension(product, anchor)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, anchor)
    capture = Capture()
    bot._write_last_verified_snapshot(capture, product)
    # Same routing invariant as policy.apply_policy: only future and
    # recovery_future hold the fixed core futures sleeve.
    assert f"期货合计：{core + .5 + .5:g}倍" in capture.text


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_parameter_query_discloses_current_fear_grid(monkeypatch, product):
    capture = Capture()
    monkeypatch.setattr(bot, "poe", capture)
    with bot.runtime_clock(datetime(2026, 10, 8, 16, tzinfo=bot.BEIJING)):
        bot.ICIMMainlinesBot()._handle_params((product,))
    assert "恐慌" in capture.text
    assert "25" in capture.text and "50" in capture.text
    assert "来源" in capture.text


def _failed_report(monkeypatch):
    capture = Capture()
    monkeypatch.setattr(bot, "poe", capture)
    def fail(*args, **kwargs):
        raise RuntimeError("MO chain source-date mismatch")
    monkeypatch.setattr(bot, "build_live_signal_with_transport_retry", fail)
    with bot.runtime_clock(datetime(2026, 9, 30, 16, tzinfo=bot.BEIJING)):
        bot.ICIMMainlinesBot()._handle_signal(("IC", "IM"), "close")
    assert "MO chain source-date mismatch" in capture.text
    return capture.text


def test_catch_up_retains_actual_product_failure_reason(monkeypatch):
    report = _failed_report(monkeypatch)
    class Store:
        def load_latest(self):
            return {"verified_day": "2026-09-29", "sequence": 1, "digest": "d"}
    coordinator = server.LedgerCoordinator.__new__(server.LedgerCoordinator)
    coordinator.store = Store()
    coordinator.lock = threading.RLock()
    coordinator.last_refresh_error = None
    monkeypatch.setattr(coordinator, "execute_query", lambda *a, **kw: (report, [], {}))
    assert coordinator.catch_up_once(datetime(2026, 9, 30, 16, tzinfo=bot.BEIJING)) is False
    assert "MO chain source-date mismatch" in coordinator.last_refresh_error


def test_realtime_digest_retains_actual_product_failure_reason(monkeypatch, tmp_path):
    report = _failed_report(monkeypatch)
    class Store:
        def __init__(self, *args):
            pass
        def load_latest(self):
            return {"verified_day": "2026-09-29", "sequence": 1, "digest": "d"}
    class Coordinator:
        def __init__(self, *args):
            pass
        def catch_up_until_current(self, *args, **kwargs):
            return 0
        def health(self, *args):
            return {"status": "ok", "verified_day": "2026-09-29"}
        def execute_query(self, *args, **kwargs):
            return report, [], {}
    monkeypatch.setattr(digest, "StateStore", Store)
    monkeypatch.setattr(digest, "LedgerCoordinator", Coordinator)
    with pytest.raises(RuntimeError, match="MO chain source-date mismatch"):
        digest.build_artifacts(state_dir=tmp_path / "state", out_dir=tmp_path / "out",
                               clock=datetime(2026, 9, 30, 14, tzinfo=bot.BEIJING),
                               max_sessions=1, mode="realtime")
