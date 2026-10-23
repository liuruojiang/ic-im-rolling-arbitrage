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


@pytest.mark.parametrize("clock,query", [("2026-10-22T08:00:00+08:00","当前持仓"),("2026-10-22T09:10:00+08:00","当前持仓"),("2026-10-22T09:30:00+08:00","当前持仓"),("2026-10-22T14:30:00+08:00","当前持仓"),("2026-10-21T18:00:00+08:00","信号")])
def test_readonly_snapshot_uses_latest_verified_record(monkeypatch,tmp_path,clock,query):
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
    if query=="信号":
        def unavailable(*_a,**_k): raise RuntimeError("controlled source unavailable")
        monkeypatch.setattr(digest.strategy,"build_live_signal_with_transport_retry",unavailable)
    report,attachments,observed=coordinator.execute_query(query,datetime.fromisoformat(clock),persist_confirmed=False)
    artifact=Path(__file__).parent/("snapshot_"+clock.replace(":","").replace("+", "_")+".md")
    artifact.write_text(report,encoding="utf-8")
    assert observed=={} and not attachments
    assert coordinator.store.load_latest()["digest"]==before
    assert report.count("最后逐腿核验快照（2026-10-21") == 2
    assert "最后逐腿核验快照（2026-10-20" not in report
