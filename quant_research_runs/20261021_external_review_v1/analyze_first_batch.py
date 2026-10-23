"""Read-only normalization and local checkpoint audit; no return recomputation."""
from pathlib import Path
import hashlib
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
relative = "quant_param_scan_runs/20260918_ic_im_ic_rebuild_l9_short_put_sizing_q1_qd05_qd10/daily_outputs/quantity_sizing.csv"
path = ROOT / relative
frame = pd.read_csv(path)
chosen = frame.loc[frame.scope.eq("real") & frame.quantity_mode.eq("q_delta05")].copy()
base_equivalent = chosen.contracts_per_1_ic / chosen.quantity_scale
actual_delta = chosen.contracts_for_fixed_core_0_5x * chosen.decision_known_entry_abs_delta / base_equivalent
expected_core_qty = .5 * base_equivalent * .5 / chosen.decision_known_entry_abs_delta
receipt = dict(
    quantity_source=relative, quantity_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    real_sizing_rows=len(chosen),
    max_quantity_difference_from_live_formula=float((chosen.contracts_for_fixed_core_0_5x-expected_core_qty).abs().max()),
    reported_normalized_delta_range=[float(chosen.effective_entry_delta_per_1_ic.min()),
                                     float(chosen.effective_entry_delta_per_1_ic.max())],
    actual_core_delta_per_account_1x_range=[float(actual_delta.min()),float(actual_delta.max())],
    conclusion="Live formula matches researched 0.5x fixed-core scaling; text normalization conflicts remain. Do not double quantity.",
    limitations="Does not establish unseen user intent or re-estimate historical strategy returns.")
checkpoint = ROOT / "runtime/ic_im_v1_4_r1/latest.json"
record = json.loads(checkpoint.read_text(encoding="utf-8-sig"))
anchor = record["products"]["IC"]
keys = ["v14_core_put_contract", "post_put_contract", "v14_short_put_contract",
        "v14_profit_old_contract", "v14_profit_reentry_contract"]
receipt["local_checkpoint"] = dict(verified_day=record["verified_day"],sequence=record["sequence"],
    contracts={key:anchor.get(key) for key in keys},
    adjusted_contracts_present=any("A" in str(anchor.get(key) or "") for key in keys),
    scope="Local checkpoint only, not proof of current broker holdings or latest cloud ledger")
(HERE/"first_batch_analysis.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(receipt,ensure_ascii=False,indent=2))
