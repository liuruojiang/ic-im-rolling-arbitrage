"""A known primary listing failure must survive the captured success envelope."""
from copy import deepcopy
import json

from test_surface_v5_r1 import bot, evidence, DAY, CLOCK, HERE, wire_sources, offline


def test_captured_sina_fallback_preserves_observed_primary_failure(monkeypatch, tmp_path):
    calls = wire_sources(monkeypatch, "sina")
    live_outcomes = []
    def builder(*a, **kw):
        months = bot.fetch_510500_expiries_with_failover()
        live_outcomes.append(deepcopy(bot._IC_LISTING_QUERY_CACHE.get()["listing"]))
        return {"product": "IC", "market_date": DAY, "months": months}
    monkeypatch.setattr(bot, "_build_live_trade_signal", builder)
    with evidence.capture_session(tmp_path / "archive", DAY, CLOCK, {"scope": "fallback provenance audit"}) as session:
        result = bot.build_live_trade_signal("IC", CLOCK, "close")
    reader = evidence.load_session(tmp_path / "archive", DAY)
    stored = reader.call("ic.months", {})
    detail = {"calls": calls, "live_outcome": live_outcomes[0], "stored_outcome": stored,
              "session_complete": session.context["complete"], "session_failures": session.context["failures"],
              "public_months": result["months"]}
    (HERE / "fallback_provenance_observation.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    assert len(live_outcomes[0]["failures"]) == 1
    assert stored["failures"] == live_outcomes[0]["failures"], "captured available listing discarded known SSE failure"
