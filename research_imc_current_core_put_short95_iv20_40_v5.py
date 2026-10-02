"""Lower-IV scan on the corrected IMC/core-Put/short-Put replay.

Only the short-Put IV admission threshold changes.  Premium-decay rolling is
fixed at 60%, and the v4 early-valuation, accounting, and timing corrections
remain mandatory.  Research only; no production configuration is changed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import research_imc_current_core_put_decay50_60_router_fresh_v1 as pair
import research_imc_current_core_put_decay60_router_fresh_v1 as common
import research_imc_current_core_put_short95_earlyvaluation_v4 as v4

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_imc_put_iv_95_put_lower_iv_decay60_earlyvaluation_short95_put_iv_20_22_24_26_28_30_32_5_35_37_5_40_decay60"
THRESHOLDS = (0.20, 0.22, 0.24, 0.26, 0.28, 0.30, 0.325, 0.35, 0.375, 0.40)


def main() -> None:
    v4.RUN = RUN
    common.THRESHOLDS = THRESHOLDS
    pair.DECAYS = (0.60,)
    v4.main()

    real_signal = pd.read_csv(RUN / "daily_outputs" / "real_signal_audit.csv", parse_dates=["eval_date"])
    model_signal = pd.read_csv(RUN / "daily_outputs" / "model_signal_audit.csv", parse_dates=["eval_date"])
    early = model_signal[model_signal.eval_date.le(pd.Timestamp("2015-10-16"))]
    if int(early.short_put_permission.sum()) != 0:
        raise RuntimeError("Early certified tier-3 interval incorrectly permits short Put entry")
    if not early.valuation_source.eq("certified_early_tier3").all():
        raise RuntimeError("Early valuation source was not fully restored")

    trades = pd.read_csv(RUN / "daily_outputs" / "core_put_trades.csv", parse_dates=["signal_eval_date", "actual_execution_date"])
    model_core = trades[trades.candidate.eq("model_monthly_imc_current_core_put102")]
    first = model_core.sort_values("actual_execution_date").iloc[0]
    if first.signal_eval_date != pd.Timestamp("2015-04-15") or first.actual_execution_date != pd.Timestamp("2015-04-16"):
        raise RuntimeError("Initial reconstructed core-Put execution is missing")

    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["scan_type"] = "lower_iv_scan_decay60_with_certified_early_valuation"
    meta["parameter_group"] = "short95 Put IV 20/22/24/26/28/30/32.5/35/37.5/40%; decay fixed 60%"
    meta["source_hashes"]["v5_wrapper"] = common.sha(Path(__file__))
    meta["validation"] = {
        "early_model_rows": int(len(early)),
        "early_short_put_permissions": int(early.short_put_permission.sum()),
        "early_valuation_sources": early.valuation_source.value_counts().to_dict(),
        "first_model_core_put_eval": str(first.signal_eval_date.date()),
        "first_model_core_put_execution": str(first.actual_execution_date.date()),
    }
    meta["warnings"] = [
        warning for warning in meta["warnings"]
        if "three IV35 cycles" not in warning
    ]
    meta["warnings"].insert(
        1,
        "Lower IV thresholds deliberately increase route frequency; real history remains short and cycle counts must accompany performance.",
    )
    meta["decision"] = "research_only_pending_lower_iv_interpretation"
    meta["stability_label"] = "lower_iv_scan_pending_review"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record_path = RUN / "record.md"
    record = record_path.read_text(encoding="utf-8")
    record = record.replace(
        "# 高IV卖Put路由对抗复算v4：恢复2015早期估值",
        "# 卖Put IV 20%—40%扫描v5：60%衰减与早期估值修正版",
    )
    record = record.replace(
        "本版保留IMC parity、因果开仓、下一交易日交割转IM和周期账本修复，并恢复既有2015早期估值认证：缺失score的244日为3档，未知失败关闭。",
        "本轮只扫描卖95% Put的IV门槛，60%权利金衰减、当前核心Put、IMC会计、因果时点和2015早期估值恢复全部固定不变。",
    )
    record_path.write_text(record, encoding="utf-8")


if __name__ == "__main__":
    main()
