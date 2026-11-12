"""A public list must never override a contradictory source outcome."""
from copy import deepcopy

import pytest

from test_available_outcome_preservation import offline
from test_surface_v5_r1 import bot, evidence, adapter, wire_sources, DAY, CLOCK, LISTED


@pytest.mark.parametrize("mismatch", ["months", "status"])
def test_capture_rejects_public_list_source_outcome_disagreement_and_restores_cache(monkeypatch, tmp_path, mismatch):
    wire_sources(monkeypatch, "available")
    def inconsistent(outcome):
        cache = bot._IC_LISTING_QUERY_CACHE.get()
        assert cache is not None and cache["listing"]["status"] == "available"
        if mismatch == "months":
            return ["202611", "202612", "202703", "202709"]
        cache["listing"] = {
            "revision": adapter.LISTING_OUTCOME_REVISION, "status": "transport_unavailable", "months": [],
            "failures": [{"source": source, "kind": "transport", "error_type": "Timeout", "error": "controlled"}
                         for source in ["SSE", "Sina"]]}
        return list(LISTED)
    monkeypatch.setattr(adapter, "listing_value", inconsistent)
    token = bot._IC_LISTING_QUERY_CACHE.set(None)
    try:
        with evidence.capture_session(tmp_path / "archive", DAY, CLOCK, {}) as session:
            with pytest.raises(evidence.EvidenceUnavailable, match="公开值与来源结果不一致"):
                bot.fetch_510500_expiries_with_failover()
            assert bot._IC_LISTING_QUERY_CACHE.get() is None
        assert not session.context["complete"]
        assert not any(row["role"] == "ic.months" for row in session.entries.values())
        assert any(row["role"] == "ic.months" and not row["resolved"] for row in session.context["failures"])
    finally:
        bot._IC_LISTING_QUERY_CACHE.reset(token)
