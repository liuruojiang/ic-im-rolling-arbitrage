"""Compare the frozen high-IV IMC->short-95% router with 60% early-roll Put management.

Research-only.  The IV router, admission rule, IMC fallback, cash treatment,
cash-settlement -> IM recovery and breakeven exit are inherited unchanged.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd

import research_imc_high_iv_short95_router_v1 as real
import research_imc_high_iv_short95_router_model_v1 as model
import research_im_short_put_recovery_atm_full_model_v1 as model_source

ROOT = Path(__file__).resolve().parent
REAL_RUN = ROOT / "quant_param_scan_runs" / "20260916_imc_high_iv_short95_router_decay60_real_v1"
MODEL_RUN = ROOT / "quant_param_scan_runs" / "20260916_imc_high_iv_short95_router_decay60_model_v1"
DECAY = 0.60


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runner():
    """Instrument the proven router state machine without touching its source."""
    source = inspect.getsource(real.run_router)
    source = source.replace(
        "def run_router(base: pd.DataFrame, options: pd.DataFrame, futures: pd.DataFrame, signal: pd.DataFrame, threshold: float, fallback: str)",
        "def run_router_decay(base: pd.DataFrame, options: pd.DataFrame, futures: pd.DataFrame, signal: pd.DataFrame, threshold: float, fallback: str, early_roll_threshold: float | None = None)",
    )
    source = source.replace(
        'candidate = route_label(threshold, fallback)',
        'candidate = route_label(threshold, fallback) + ("" if early_roll_threshold is None else f"__decay{int(early_roll_threshold * 100)}")',
    )
    source = source.replace(
        'pending = ""\n    cycle:', 'pending = ""\n    pending_roll = False\n    cycle:'
    )
    source = source.replace(
        '"expiry": pd.Timestamp(oq.actual_expiry)}',
        '"expiry": pd.Timestamp(oq.actual_expiry), "contract_month": pd.Timestamp(oq.contract_month), "entry_premium": float(oq.open), "rolled": False, "roll_wait_reason": ""}',
    )
    old = '''        elif state == "put":
            q = option_lookup.loc[(position["contract"], day)]
            pnl += position["units"] * 200 * (position["mark"] - float(q.settle))
            position["mark"] = float(q.settle)
'''
    new = '''        elif state == "put":
            q = option_lookup.loc[(position["contract"], day)]
            pnl += position["units"] * 200 * (position["mark"] - float(q.settle))
            position["mark"] = float(q.settle)
            if pending_roll:
                # The new leg must independently pass the existing admission
                # signal on this execution day.  IV does not need to re-trigger:
                # this is management of an already high-IV-routed cycle.
                target_month = pd.Timestamp(position["contract_month"]) + pd.offsets.MonthBegin(1)
                chain = options_by_day.get(day, options.iloc[:0])
                chain = chain[chain.contract_month.eq(target_month)].copy()
                if permitted and not chain.empty:
                    prior_spot = float(base.iloc[i - 1].csi1000_price_close)
                    chain["distance"] = (chain.strike - prior_spot * .95).abs()
                    selected = chain.sort_values(["distance", "strike", "contract"]).iloc[0]
                    valid = bool(selected.open > 0 and selected.volume > 0 and selected.open_interest > 0)
                    if valid:
                        # Both old-cover and new-sale are T+1 opens, after the
                        # old/new close marks above; reset the decay reference.
                        pnl += position["units"] * 200 * (float(q.settle) - float(q.open))
                        pnl += position["units"] * 200 * (float(selected.open) - float(selected.settle))
                        cost += position["units"] * 200 * prior_spot * (2 * ONE_WAY_COST)
                        position.update(contract=str(selected.contract), mark=float(selected.settle), expiry=pd.Timestamp(selected.actual_expiry), contract_month=pd.Timestamp(selected.contract_month), entry_premium=float(selected.open), rolled=True, roll_wait_reason="")
                        cycle["early_rolls"] = int(cycle.get("early_rolls", 0)) + 1
                        cycle["last_early_roll_date"] = str(day.date())
                        action = "put_early_roll60_buyback_and_sell_next_open"
                        pending_roll = False
                    else:
                        reason = "put_early_roll_blocked_untradable_new_leg"
                        if position.get("roll_wait_reason") != reason:
                            action = reason
                        position["roll_wait_reason"] = reason
                else:
                    reason = "put_early_roll_blocked_re_admission"
                    if position.get("roll_wait_reason") != reason:
                        action = reason
                    position["roll_wait_reason"] = reason
'''
    if old not in source:
        raise RuntimeError("Put mark hook no longer matches the frozen router")
    source = source.replace(old, new)
    hook = '''        if state == "put" and day == position["expiry"]:'''
    replacement = '''        if state == "put" and early_roll_threshold is not None and not position.get("rolled", False) and not pending_roll and not action and position["mark"] <= position["entry_premium"] * (1 - early_roll_threshold):
            pending_roll = True
            position["roll_wait_reason"] = ""
            action = f"put_premium_decay_{int(early_roll_threshold * 100)}_signal_close"
        if state == "put" and day == position["expiry"]:'''
    if hook not in source:
        raise RuntimeError("Put expiry hook no longer matches the frozen router")
    source = source.replace(hook, replacement)
    source = source.replace(
        '"decision_reason": decision_reason}',
        '"decision_reason": decision_reason, "pending_roll": pending_roll}',
    )
    ns = dict(vars(real))
    exec(compile(source, str(ROOT / "research_imc_high_iv_short95_router_decay60_v1.py"), "exec"), ns)
    return ns["run_router_decay"], source


def outputs(run: Path, layer: str, base: pd.DataFrame, options: pd.DataFrame, futures: pd.DataFrame, signal: pd.DataFrame, fn):
    run.mkdir(parents=True, exist_ok=False)
    signal.to_csv(run / "signal_audit.csv", index=False, encoding="utf-8-sig")
    parts = []
    parity = {}
    for threshold in real.THRESHOLDS:
        for fallback in ("cash_when_ineligible", "continue_imc_when_ineligible"):
            ref, _, _ = real.run_router(base, options, futures, signal, threshold, fallback)
            got, events, cycles = fn(base, options, futures, signal, threshold, fallback, None)
            error = float(np.max(np.abs(ref.return_net.to_numpy() - got.return_net.to_numpy())))
            if error > 1e-12: raise RuntimeError(f"{layer} baseline parity failed {threshold} {fallback}: {error}")
            parity[f"{threshold}_{fallback}"] = error
            improved, ievents, icycles = fn(base, options, futures, signal, threshold, fallback, DECAY)
            for frame, mode in ((got, "baseline"), (improved, "decay60")):
                frame["mode"] = mode
                parts.append(frame)
            events.assign(mode="baseline").to_csv(run / f"events_{int(threshold*1000)}_{fallback}_baseline.csv", index=False)
            ievents.assign(mode="decay60").to_csv(run / f"events_{int(threshold*1000)}_{fallback}_decay60.csv", index=False)
            cycles.assign(mode="baseline").to_csv(run / f"cycles_{int(threshold*1000)}_{fallback}_baseline.csv", index=False)
            icycles.assign(mode="decay60").to_csv(run / f"cycles_{int(threshold*1000)}_{fallback}_decay60.csv", index=False)
    daily = pd.concat(parts, ignore_index=True)
    summary, _ = real.metric_table(daily)
    summary["mode"] = summary.candidate.str.extract(r"__(decay60)$", expand=False).fillna("baseline")
    summary.to_csv(run / "scan_summary.csv", index=False, encoding="utf-8-sig")
    daily.to_csv(run / "daily.csv.gz", index=False, compression="gzip")
    diag = daily.groupby(["candidate", "mode"]).agg(
        early_roll_signals=("action", lambda x: int(x.str.contains("premium_decay_60_signal").sum())),
        early_rolls=("action", lambda x: int(x.eq("put_early_roll60_buyback_and_sell_next_open").sum())),
        blocked_re_admission=("action", lambda x: int(x.eq("put_early_roll_blocked_re_admission").sum())),
    ).reset_index()
    diag.to_csv(run / "roll_diagnostics.csv", index=False, encoding="utf-8-sig")
    meta = {"layer": layer, "baseline_parity_max_abs_return_error": parity, "decay_rule": "T-close premium <=40% of entry; T+1 open close old and sell only the immediately-next month; one early roll; current existing admission only", "admission": "valuation tier <=1 AND MOM120 >=0; existing IV router unchanged", "decision": "research_only_pending_comparison", "source_hashes": {"harness": digest(Path(__file__)), "router": digest(ROOT / "research_imc_high_iv_short95_router_v1.py")}}
    (run / "scan_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary, diag, parity


def main() -> None:
    fn, executed = runner()
    base = pd.read_csv(real.BASE, parse_dates=["date"])
    raw = pd.read_csv(real.OPTIONS, parse_dates=["date"]); raw["contract_month"] = pd.to_datetime("20" + raw.contract.str[2:6], format="%Y%m")
    real_options = real.prepare_options(raw, real.actual_expiry_map(raw, base)); futures = pd.read_csv(real.FUTURES, parse_dates=["date"]); signal = real.prepare_signal(base, real_options)
    real_summary, real_diag, _ = outputs(REAL_RUN, "real IM/MO", base, real_options, futures, signal, fn)
    market, model_base, model_options, model_futures, _, _ = model_source.build_inputs(); model_options = model_options.copy(); model_options["close"] = model_options["settle"]; model_signal = real.prepare_signal(model_base, model_options)
    model_summary, model_diag, _ = outputs(MODEL_RUN, "theoretical proxy", model_base, model_options, model_futures, model_signal, fn)
    (REAL_RUN / "executed_harness.py").write_text(executed, encoding="utf-8")
    (MODEL_RUN / "executed_harness.py").write_text(executed, encoding="utf-8")
    print(real_summary[real_summary.segment.eq("full")].to_string(index=False)); print(real_diag.to_string(index=False)); print(model_summary[model_summary.segment.eq("full")].to_string(index=False))


if __name__ == "__main__": main()
