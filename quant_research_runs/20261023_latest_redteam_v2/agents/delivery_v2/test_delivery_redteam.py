from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import pytest
import run_ic_im_v1_4_github_digest as digest
from test_run_ic_im_v1_4_github_digest import _real_unpublished_result, _apply_previous_session_evidence

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(digest.strategy.requests, "get", lambda *a, **k: pytest.fail("unexpected HTTP"))


def install_observed(monkeypatch, latest, observed):
    class Store:
        def __init__(self, *_a): pass
        def load_latest(self): return deepcopy(latest)
    class Coordinator:
        def __init__(self, *_a): pass
        def catch_up_until_current(self, *_a, **_k): return 0
        def health(self, *_a): return {"status":"ok", "verified_day":latest["verified_day"]}
        def execute_query(self, *_a, **_k): return "controlled intraday source", [], deepcopy(observed)
    monkeypatch.setattr(digest,"StateStore",Store)
    monkeypatch.setattr(digest,"LedgerCoordinator",Coordinator)

@pytest.mark.parametrize("finished", ["2026-10-14T14:59:59+08:00", "2026-10-14T15:00:00+08:00", "2026-10-14T15:00:01+08:00"])
def test_realtime_completion_must_stay_before_close(monkeypatch, tmp_path, finished):
    original=_real_unpublished_result()
    observed=deepcopy(original["signals"])
    _apply_previous_session_evidence(observed)
    for signal in observed.values():
        signal["signal_generated_at"]=finished
        signal["signal_generation_timing"]="intraday_hypothetical_if_now_close"
    latest={k:original[k] for k in ["sequence","digest","verified_day"]}
    install_observed(monkeypatch, latest, observed)
    finished_clock=datetime.fromisoformat(finished)
    class CompletionClock(datetime):
        @classmethod
        def now(cls,tz=None):return finished_clock.astimezone(tz) if tz else finished_clock.replace(tzinfo=None)
    monkeypatch.setattr(digest,"datetime",CompletionClock)
    # This independent evidence-check clock follows the end of the real query.
    monkeypatch.setattr(digest.strategy.fear_grid,"_validation_clock",lambda: finished_clock)
    kwargs=dict(state_dir=tmp_path/"state",out_dir=tmp_path/"out",clock=datetime.fromisoformat("2026-10-14T14:59:50+08:00"),max_sessions=1,mode="realtime",expected_market_date="2026-10-14")
    if finished_clock.hour>=15:
        with pytest.raises(RuntimeError, match="盘中|窗口|交易|完成"):
            digest.build_artifacts(**kwargs)
    else:
        result=digest.build_artifacts(**kwargs)
        assert result["status"]=="ok"
        assert result["report_generated_at"]==finished


@pytest.mark.parametrize("clock", ["2026-10-22T08:00:00+08:00", "2026-10-22T09:10:00+08:00", "2026-10-22T09:30:00+08:00", "2026-10-22T14:30:00+08:00"])
def test_readonly_snapshot_uses_latest_verified_record(monkeypatch,tmp_path,clock):
    import shutil
    import poe_ic_im_v1_4_server as server
    import poe_ic_im_v1_4_state as state
    root=Path(__file__).resolve().parents[4]
    source=root/"runtime/ic_im_v1_4_r1"
    copied=tmp_path/"ledger_copy"
    shutil.copytree(source,copied,ignore=shutil.ignore_patterns(".ledger.lock"))
    monkeypatch.setenv("ICIM_EVIDENCE_DIR",str(tmp_path/"empty_evidence"))
    monkeypatch.setattr(digest.strategy,"LIVE_CONTINUATION_ANCHOR",deepcopy(digest.strategy.LIVE_CONTINUATION_ANCHOR))
    coordinator=server.LedgerCoordinator(state.StateStore(copied))
    latest=coordinator.store.load_latest()
    before=latest["digest"]
    assert latest["verified_day"]=="2026-10-21"
    report,attachments,observed=coordinator.execute_query("当前持仓",datetime.fromisoformat(clock),persist_confirmed=False)
    (tmp_path/"snapshot_report.md").write_text(report,encoding="utf-8")
    assert observed=={} and not attachments
    assert coordinator.store.load_latest()["digest"]==before
    assert report.count("最后逐腿核验快照（2026-10-21") == 2
    assert "最后逐腿核验快照（2026-10-20" not in report
