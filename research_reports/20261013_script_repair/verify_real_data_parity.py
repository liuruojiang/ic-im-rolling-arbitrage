"""Compare backed-up/current code on saved real data, without live requests."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BACKUP = ROOT / ".codex_backups/20261013_105427"
EVIDENCE = Path("D:/Codex/home/automations/ic-im/runs/20261012/recovery_review")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def worker(mode):
    temp = Path(tempfile.mkdtemp(prefix=f"real_parity_{mode}_", dir=OUT))
    os.environ.update(ICIM_STATE_DIR=str(temp / "import_state"), ICIM_REQUIRE_MIGRATION="0",
                      PYTHONDONTWRITEBYTECODE="1", TEMP=str(temp), TMP=str(temp))
    sys.path.insert(0, str(ROOT))
    if mode == "baseline":
        sys.path.insert(0, str(BACKUP))
    import pandas as pd
    import poe_ic_im_mainline_v1_4_bot as bot
    import poe_ic_im_v1_4_state as state
    clock = datetime(2026, 10, 12, 20, 0, tzinfo=bot.BEIJING)
    results = {"mode": mode, "scope": "Captured real OHLCV and saved research-model ledger; no performance replay or account evidence",
               "code_paths": {"bot": bot.__file__, "state": state.__file__}, "market": {}, "ledger": [],
               "older_schema_days_not_rewritten": []}
    candidate = EVIDENCE / "tencent_full_history_candidate_20261012"
    for product, pages in (("IC", 8), ("IM", 5)):
        paths = [candidate / f"raw_{product.lower()}_page_{index:02d}.bin" for index in range(1, pages + 1)]
        responses = []
        class Response:
            status_code = 200
            def __init__(self, path):
                self.content = path.read_bytes()
                self.headers = {"Content-Length": str(len(self.content))}
                self.url = f"captured-response:{path.name}"
                self.closed = False
            def raise_for_status(self):
                pass
            def iter_content(self, chunk_size):
                for offset in range(0, len(self.content), chunk_size):
                    yield self.content[offset:offset + chunk_size]
            def close(self):
                self.closed = True
        def saved_get(url, **kwargs):
            assert kwargs["stream"] is True
            response = Response(paths[len(responses)])
            responses.append(response)
            return response
        with patch.object(bot.requests, "get", side_effect=saved_get), bot._network_budget(30):
            tencent = bot._fetch_tencent_ohlcv_history(product, clock)
        source_path = EVIDENCE / "official_source_fetch_20261012_1735" / f"{product.lower()}_validated_ohlcv.csv"
        sina = pd.read_csv(source_path, parse_dates=["date"]).set_index("date")
        sina = bot._validate_v13_ohlcv(product, sina, clock)
        schedules = {}
        for source, prices in (("tencent", tencent), ("sina", sina)):
            schedule = bot.v13_momentum_schedule(product, prices.close, ohlcv=prices)
            serialized = schedule.to_json(orient="split", date_format="iso", double_precision=15)
            schedules[source] = {"rows": len(schedule), "columns": list(schedule.columns),
                                 "sha256": hashlib.sha256(serialized.encode()).hexdigest(),
                                 "last_signal_target": float(schedule.signal_target.iloc[-1])}
        results["market"][product] = {"rows": len(tencent), "first": str(tencent.index[0].date()),
            "last": str(tencent.index[-1].date()), "pages": len(responses),
            "all_responses_closed": all(response.closed for response in responses),
            "schedules": schedules,
            "inputs": [{"path": str(path), "sha256": sha(path)} for path in [*paths, source_path]]}
    journal = sorted((ROOT / "runtime/ic_im_v1_4_r1/journal").glob("*.json"))
    for previous_path, next_path in zip(journal, journal[1:]):
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        expected = json.loads(next_path.read_text(encoding="utf-8"))
        if expected["signals"]["IC"].get("v14_build_id") != "v1.4-20261008-r1-ic-csi500-abs40-fix11":
            results["older_schema_days_not_rewritten"].append(expected["verified_day"])
            continue
        # Every step uses the exact original preceding record; the complete hash
        # chain through that record is copied into this isolated replay store.
        destination = temp / f"ledger_{expected['sequence']:03d}"
        (destination / "journal").mkdir(parents=True)
        for source in journal:
            if source.name <= previous_path.name:
                shutil.copyfile(source, destination / "journal" / source.name)
        shutil.copyfile(previous_path, destination / "latest.json")
        store = state.StateStore(destination)
        prior = store.load_latest()
        observed = store.append_confirmed_signals(prior, deepcopy(expected["signals"]))
        reloaded = store.load_latest()
        results["ledger"].append({"sequence": expected["sequence"], "day": expected["verified_day"],
             "source_sha256": sha(next_path), "restart_digest_equal": reloaded["digest"] == observed["digest"],
             "canonical_products_equal": observed["products"] == expected["products"],
             "products": observed["products"]})
    (OUT / f"real_parity_{mode}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    if len(sys.argv) == 2:
        worker(sys.argv[1])
        return
    for mode in ("baseline", "current"):
        subprocess.run([sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()), mode],
                       cwd=ROOT, check=True, timeout=180)
    before = json.loads((OUT / "real_parity_baseline.json").read_text(encoding="utf-8"))
    after = json.loads((OUT / "real_parity_current.json").read_text(encoding="utf-8"))
    result = {"scope": after["scope"], "market_equal": before["market"] == after["market"],
              "saved_ledger_steps_equal": before["ledger"] == after["ledger"],
              "ledger_step_count": len(after["ledger"]),
              "older_schema_days_not_rewritten": after["older_schema_days_not_rewritten"],
              "all_ledger_restarts_verified": all(row["restart_digest_equal"] for row in after["ledger"]),
              "all_products_equal_canonical": all(row["canonical_products_equal"] for row in after["ledger"]),
              "rows": {product: data["rows"] for product, data in after["market"].items()},
              "cutoff": "2026-10-12", "code_paths": {"baseline": before["code_paths"], "current": after["code_paths"]}}
    (OUT / "real_data_parity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert result["market_equal"] and result["saved_ledger_steps_equal"] and result["all_ledger_restarts_verified"]


if __name__ == "__main__":
    main()
