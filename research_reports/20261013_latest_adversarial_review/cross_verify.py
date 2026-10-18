"""Primary reviewer's independent rerun and explicit symptom/control checks.

PASS_REPRODUCED means an existing defect was reproduced, never that it is fixed.
The three subprocesses only write their own isolated review artifacts.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def read(path):
    return json.loads((HERE / path).read_text(encoding="utf-8"))


def run_script(script):
    name = Path(script).parent.name
    command = [sys.executable, "-B", "-X", "utf8", str(HERE / script)]
    started = time.perf_counter()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    with (HERE / f"primary_rerun_{name}.log").open("w", encoding="utf-8") as log:
        run = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    assert run.returncode == 0, (script, run.returncode)
    return {"script": script, "command": command, "exit_code": run.returncode,
            "wall_seconds": round(time.perf_counter() - started, 3)}


def main():
    scripts = ["policy_state/repro_policy_state.py", "market_delivery/repro_market_delivery.py",
               "strategy/verify_strategy_evidence.py"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(run_script, scripts))
    p = read("policy_state/repro_results.json")
    m = read("market_delivery/repro_results.json")
    s = read("strategy/verification_results.json")
    assert not any(isinstance(v, dict) and "error" in v for v in p.values()), p
    assert not any("harness_error" in v for v in m["probes"].values()), m
    assert p["real_saved_control"]["same_products"] is True
    control = p["ordinary_open_no_new_plan_control"]
    assert control["signal_basis"] == control["persisted_basis"] == 350.0
    assert control["persisted_eligible"] is True
    basis = p["ordinary_open_basis_lost"]
    assert basis["open_status"] == "confirmed_open_research_price"
    assert basis["new_plan_core_changed"] is False and basis["new_plan_momentum_changed"] is True
    assert basis["signal_basis"] == 350.0 and basis["persisted_basis"] is None
    assert basis["signal_eligible"] is True and basis["persisted_eligible"] is False
    route_control = p["future_route_momentum_control"]
    assert route_control["persisted_quantity"] == 0 and route_control["pending_plan"] is not None
    route = p["short_route_momentum_books_without_open"]
    assert route["target_quantity"] == route["persisted_quantity"] == 1.5
    assert route["before_quantity"] == 0 and route["pending_plan"] is None and route["open_status"] is None
    assert route["candidate_error"] is None and route["restart_digest_match"]
    timestamps = m["probes"]["same_day_quote_time"]["outcomes"]
    for outcome in timestamps:
        assert outcome["close_confirmed"] is True
        assert outcome["actual_state_store_append_and_reload"] == "accepted"
        assert outcome["close_artifact_validator"] == "accepted"
    assert {x["injected_quote_time"] for x in timestamps} == {"09:31:00", "23:59:59", "15:00:00"}
    assert any(x["injected_quote_time"] == "09:31:00" and x["v14_action"] == "ENTER_SHORT_PUT" for x in timestamps)
    report = m["probes"]["split_ic_summary"]
    assert report["official_core_plan"]["new_contract"] == "510500P2612M07500"
    assert report["official_momentum_plan"]["new_contract"] == "510500P2612M07300"
    assert "510500P2612M07300" in report["rendered_core_summary"] and "数量：10" in report["rendered_core_summary"]
    assert m["canonical_hashes_unchanged"] is True
    for product, replay in m["probes"]["real_tencent_replay"].items():
        assert replay["last"] == "2026-10-12" and replay["all_responses_closed"]
        assert replay["recent60_volume_max_abs_diff"] == 0
        assert replay["current_signal_target_sina"] == replay["current_signal_target_tencent"]
    assert all(x["matches_saved_summary"] for x in s["source_hash_checks"])
    assert all(x["nav_identity_max_error"] < 1e-10 for x in s["saved_metric_checks"])
    assert all(x["core_units_after_policy"] == 0.5 and x["protection_after_policy"] == 0
               for x in s["synthetic_recovery_policy_probes"])
    protected = read("protected_files_before.json")
    differences = [path for path, expected in protected.items()
                   if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != expected]
    assert not differences, differences
    result = {"status": "PASS_REPRODUCED_FOUR_UNFIXED_SCRIPT_FINDINGS",
              "scripts": runs, "findings_reproduced": ["PS-01", "PS-02", "MARKET-P1-01", "REPORT-P2-01"],
              "controls_confirmed": ["saved_real_ledger_products", "new_core_without_new_plan",
                                     "future_route_preserves_pending", "normal_close_snapshot"],
              "strategy_saved_input_hash_count": len(s["source_hash_checks"]),
              "strategy_saved_metric_rows": len(s["saved_metric_checks"]),
              "synthetic_recovery_probes": len(s["synthetic_recovery_policy_probes"]),
              "protected_files_unchanged": True, "protected_file_count": len(protected),
              "unfixed_defects": True,
              "scope": "Review only. Synthetic fault injection is not an observed production incident or a new strategy backtest."}
    (HERE / "primary_cross_verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
