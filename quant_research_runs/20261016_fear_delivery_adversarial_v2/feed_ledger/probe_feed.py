"""Independent read-only fault injection; writes only this audit directory."""
import copy
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import pytest
import ic_im_fear_grid_v1 as feed
import poe_ic_im_v1_4_state as state
from test_ic_im_fear_source_delivery_20261016 import setup_feed, signal_for

OUT = Path(__file__).parent
DAY = date(2026, 10, 16)
results = []

def record(name, signal, expectation):
    try:
        state.validate_fear_grid_signal(signal, "IC")
        actual = "accepted"
        error = None
    except Exception as exc:
        actual = "rejected"
        error = f"{type(exc).__name__}: {exc}"
    view = feed.publication_view(signal)
    results.append(dict(name=name, expectation=expectation, actual=actual, error=error,
                        visible_grid_target=view.get("grid_target"),
                        visible_completeness=view.get("signal_completeness"), signal=signal))

with pytest.MonkeyPatch.context() as mp:
    setup_feed(mp, page_ok=False)
    original = feed.fetch_score(DAY, "close")
    record("csv_only_legitimate", signal_for(original), "accepted")
    changed = copy.deepcopy(original)
    changed["fear_greed_index"] = 80
    record("csv_latest_value_tamper", signal_for(changed), "rejected: chosen value must match CSV latest value on the same date")
    changed = copy.deepcopy(original)
    changed.update(fear_fallback_applied=True, fear_previous_exchange_date="2026-10-15",
                   fear_data_date="2026-10-15", fear_data_status=feed.CLOSE_FALLBACK_STATUS,
                   fear_fallback_reason="same_day_not_published", fear_fallback_timing="same_day_post_close")
    record("false_fallback_while_today_published", signal_for(changed), "rejected: evidence explicitly contains a valid T value")
    changed = signal_for(original)
    changed.update(signal_generated_at="2026-10-17T00:00:01+08:00", signal_generation_timing="close_confirmed")
    mp.setattr(feed, "_validation_clock", lambda: datetime(2026,10,17,0,1,tzinfo=feed.BEIJING))
    record("next_day_generation_without_replay_identity", changed, "rejected: actual generation is next day and must be labelled retrospective_replay")

with pytest.MonkeyPatch.context() as mp:
    setup_feed(mp, csv_ok=False, hour=13)
    # Use the page parser seam to hold a real-shaped intraday page clock.
    mp.setattr(feed, "_parse_page", lambda raw: (DAY, 20., datetime(2026, 10, 16, 12, 0, tzinfo=feed.BEIJING)))
    original = feed.fetch_score(DAY, "intraday")
    signal = signal_for(original)
    signal["close_confirmed"] = False
    record("page_only_intraday_legitimate", signal, "accepted")
    changed = copy.deepcopy(original)
    changed["fear_greed_index"] = 80
    signal = signal_for(changed)
    signal["close_confirmed"] = False
    record("page_value_tamper", signal, "rejected: chosen page value must match its evidence")

with pytest.MonkeyPatch.context() as mp:
    setup_feed(mp, csv_ok=False, page_ok=False, hour=15)
    original = feed.fetch_score(DAY, "intraday")
    signal = signal_for(original)
    signal["close_confirmed"] = False
    record("intraday_request_completes_after_15_missing", signal, "unavailable warning / partial publication (window closure is an expected no-data state)")

with pytest.MonkeyPatch.context() as mp:
    setup_feed(mp)
    mp.setattr(feed, "_bounded_get", lambda url, maximum: (
        b"date,fear_greed_index\n2026-10-15,20\n" if url == feed.CSV_URL else b"page"))
    mp.setattr(feed, "_parse_page", lambda raw: (date(2026,10,15), 20., datetime(2026,10,15,11,0,tzinfo=feed.BEIJING)))
    original = feed.fetch_score(DAY, "close")
    record("prior_day_provisional_matched", signal_for(original), "accepted only with explicit missing final-close provenance warning")

manifest = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
            ["ic_im_fear_grid_v1.py", "poe_ic_im_v1_4_state.py", "poe_ic_im_mainline_v1_4_bot.py"]}
(OUT / "results.json").write_text(json.dumps({"source_sha256":manifest,"cases":results}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps([{k:v for k,v in x.items() if k != "signal"} for x in results], ensure_ascii=True, indent=2))
