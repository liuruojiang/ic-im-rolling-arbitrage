"""Isolated four-arm listed-history audit; never writes a frozen baseline."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STUDY = ROOT / "quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness"
PAIRED = ROOT / "quant_param_scan_runs/20260928_fear_entry_paired_exit_fix9_certified_v2"
NATIVE = ROOT / "outputs/re_certification/icim_v14_fix4_recert_20260924/l5_native_signal_reconstruction_20260925"
sys.path[:0] = [str(ROOT), str(STUDY), str(NATIVE)]
import run_study as study


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metrics(daily, start=None):
    z = daily if start is None else daily.loc[pd.to_datetime(daily.date) >= start]
    r = z.return_net.to_numpy(float)
    nav = np.r_[1., np.cumprod(1+r)]
    sd = np.std(r, ddof=1)
    return dict(start=str(z.date.iloc[0]), end=str(z.date.iloc[-1]), rows=len(z),
                cagr=float(nav[-1]**(252/len(r))-1), max_dd=float((nav/np.maximum.accumulate(nav)-1).min()),
                sharpe=float(np.mean(r)/sd*np.sqrt(252)) if sd else 0., final_nav=float(nav[-1]),
                fees=float(z.fees_cumulative.iloc[-1]- (0 if start is None else z.fees_cumulative.iloc[0])))


def compare(daily, reference):
    assert daily.date.astype(str).tolist() == reference.date.astype(str).tolist()
    errors = {c: float(np.max(np.abs(daily[c].to_numpy(float)-reference[c].to_numpy(float))))
              for c in ("nav", "return_net", "equity_close", "cash_close", "fees_cumulative")}
    assert errors["nav"] < 1e-10 and errors["return_net"] < 1e-10 and max(errors.values()) < 1e-6, errors
    return errors


def main():
    if (HERE/"history_manifest.json").exists():
        raise FileExistsError("Completed study cannot be overwritten")
    old_policy = load("put_repairs_original_policy", HERE/"backups/ic_im_v1_4_policy.py")
    paired = load("put_repairs_paired", PAIRED/"run_scan.py")
    listed = paired.listed
    original_life_path = HERE/"backups"/NATIVE.relative_to(ROOT)/"native_fix4_seller_lifecycle_v1.py"
    source_text = original_life_path.read_text(encoding="utf-8")
    anchor = '                if VERSION == "v1":\n                    next_anchor["v14_short_put_entry_premium"] = fill_new'
    assert source_text.count(anchor) == 1
    fixed_text = source_text.replace(anchor, '                if True:  # execution-cost repair: every new/rolled leg\n                    next_anchor["v14_short_put_entry_premium"] = fill_new')
    fixed_path = HERE/"seller_lifecycle_execution_cost.py"
    fixed_path.write_text(fixed_text, encoding="utf-8")
    fixed_life = load("put_repairs_fixed_lifecycle", fixed_path)
    audits = {}
    all_daily, all_events, summaries = [], [], []
    formal = study.FORMAL
    current_policy = formal.v14_policy
    formal.v14_policy = old_policy
    try:
        source, candidates, gov, spot, lives, folders, candidate_path, im_life_path, ic_audit = listed.load_account_inputs()
        fear = pd.read_csv(listed.FEAR_FILE).set_index("date").fear_greed_index
        original_permission = old_policy.seller_permission
        def permission(product, signal, candidate):
            if product == "IC":
                m = signal.get("momentum_120")
                weight = 1. if m is not None and np.isfinite(float(m)) and float(m) >= 0 else 0.
                signal = {**signal, "momentum_next_weight": weight}
            return original_permission(product, signal, candidate)
        reference_all = pd.read_csv(PAIRED/"outputs/fix9_full_account_daily_nav.csv.gz")
        life_changes = []
        for product in ("IC", "IM"):
            p = product.lower()
            input_folder = HERE/"inputs"/p
            input_folder.mkdir(parents=True, exist_ok=True)
            target_name = f"native_fix4_{p}_noseller_targets_{'v2' if product=='IC' else 'v3'}.csv.gz"
            risk_name = f"native_fix4_{p}_risk_signals_v1.csv.gz"
            if product == "IC":
                latest = ROOT/"quant_research_runs/20261002_fix10_vs_fix11_full_history/account_inputs/FIX11_full"
                risk = pd.read_csv(latest/risk_name)
                targets_path = latest/target_name
                ref = pd.read_csv(ROOT/"quant_research_runs/20261002_fix10_vs_fix11_full_history/account_daily_paths.csv.gz")
                ref = ref.loc[ref.arm.eq("FIX11_full")].reset_index(drop=True)
            else:
                risk0 = pd.read_csv(folders[product]/risk_name)
                risk, _, _ = paired.build_targets(risk0, fear, product, 50.)
                targets_path = folders[product]/target_name
                ref = reference_all.loc[reference_all.candidate.eq("IM_OR_ENTRY_PAIRED_FEAR_EXIT_50_LISTED_FIX9")].reset_index(drop=True)
            risk.to_csv(input_folder/risk_name, index=False, compression="gzip")
            shutil.copy2(targets_path, input_folder/target_name)
            # Seller permissions/targets stay identical to the established current-rule replay.
            lifecycle_folder = HERE/"lifecycle_inputs"/p
            lifecycle_folder.mkdir(parents=True, exist_ok=True)
            for name in (risk_name, target_name):
                shutil.copy2(folders[product]/name, lifecycle_folder/name)
            shutil.copy2(candidate_path, lifecycle_folder/"native_fix4_seller_candidates_v1.csv.gz")
            fixed_life.HERE, fixed_life.VERSION = lifecycle_folder, "v4"
            with patch.object(old_policy, "seller_permission", side_effect=permission):
                repaired, life_audit = fixed_life.run(product, "repeat_roll", source, gov, spot)
            repaired.to_csv(HERE/f"{p}_seller_lifecycle_fixed.csv.gz", index=False, compression="gzip")
            original = lives[product].reset_index(drop=True)
            original.to_csv(HERE/f"{p}_seller_lifecycle_baseline.csv.gz", index=False, compression="gzip")
            assert original.signal_date.tolist() == repaired.signal_date.tolist()
            changed = ((original.action != repaired.action) |
                       (original.active_contract_after.fillna("") != repaired.active_contract_after.fillna("")))
            for i in np.flatnonzero(changed):
                life_changes.append(dict(product=product, signal_date=original.signal_date.iloc[i],
                                         old_action=original.action.iloc[i], new_action=repaired.action.iloc[i],
                                         old_contract=original.active_contract_after.iloc[i], new_contract=repaired.active_contract_after.iloc[i]))
            # Every actual new/roll entry in the repaired path must use its recorded open.
            entries = repaired.loc[repaired.action.isin(["ENTER_SHORT_PUT", "ROLL_SHORT_PUT"])]
            assert np.allclose(entries.entry_premium_state_after, entries.fill_new_t1, atol=0, rtol=0)
            audits[product] = dict(lifecycle=life_audit, changed_seller_state_days=int(changed.sum()),
                                   original_seller_actions=original.action.value_counts().to_dict(),
                                   repaired_seller_actions=repaired.action.value_counts().to_dict(),
                                   entry_open_basis_checks=len(entries), target_sha256=sha(input_folder/target_name),
                                   risk_sha256=sha(input_folder/risk_name))
            for arm in ("baseline", "timing_only", "cost_only", "both"):
                life = repaired if arm in {"cost_only", "both"} else original
                listed.grid_scan.configure_account(input_folder)
                acc = listed.grid_scan.account_stage
                # Historical engine already freezes T's selection and fills T+1 open
                # before using T+1 close. No artificial historical timing change.
                daily, events, audit = acc.replay(product, "repeat_roll", source, life, candidates, gov, spot)
                assert len(daily) == len(risk) and np.isfinite(daily.nav).all()
                assert (daily.nav > 0).all()
                assert (daily.cash_close-daily.margin_reserved).min() >= -1e-7
                assert not events.loc[events.event.eq("trade"), "symbol"].astype(str).str.startswith("option|call|").any()
                if arm == "baseline":
                    audits[product]["baseline_saved_parity"] = compare(daily, ref)
                    baseline = daily.copy()
                if arm == "timing_only":
                    audits[product]["timing_only_daily_parity"] = compare(daily, baseline)
                if arm == "cost_only":
                    cost = daily.copy()
                if arm == "both":
                    audits[product]["both_vs_cost_only_parity"] = compare(daily, cost)
                trades = events.loc[events.event.eq("trade")]
                audits[product][arm] = dict(profit3x_resets=int(audit["profit3x_resets"]),
                                           trades=len(trades), buyer_open_proxy_count=len(audit.get("buyer_open_proxies", [])),
                                           min_free_cash=float((daily.cash_close-daily.margin_reserved).min()))
                daily["product"], daily["arm"] = product, arm
                events["product"], events["arm"] = product, arm
                all_daily.append(daily); all_events.append(events)
                end = pd.Timestamp(daily.date.iloc[-1])
                for window, years in (("full", None), ("3Y", 3), ("1Y", 1)):
                    if years and pd.Timestamp(daily.date.iloc[0]) > end-pd.DateOffset(years=years):
                        summaries.append(dict(product=product, arm=arm, window=window, status="N/A"))
                    else:
                        summaries.append(dict(product=product, arm=arm, window=window, status="OK",
                                              **metrics(daily, None if years is None else end-pd.DateOffset(years=years))))
                print(json.dumps(dict(product=product, arm=arm, **metrics(daily)), ensure_ascii=False), flush=True)
            (HERE/f"{p}_audit.json").write_text(json.dumps(audits[product],ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        pd.DataFrame(life_changes).to_csv(HERE/"seller_state_changes.csv",index=False)
        pd.concat(all_daily,ignore_index=True).to_csv(HERE/"daily_paths.csv.gz",index=False,compression="gzip")
        pd.concat(all_events,ignore_index=True).to_csv(HERE/"events.csv.gz",index=False,compression="gzip")
        pd.DataFrame(summaries).to_csv(HERE/"window_metrics.csv",index=False)
        manifest=dict(audits=audits, spec_sha256=sha(ROOT/"docs/ic_im_put_execution_repairs_20261016_v1_spec.md"),
                      historical_timing="already causal in account engine; production defect is separately repaired",
                      price_scope="Listed-history theoretical account. Inherited ordinary buyer sticky-IV open proxies remain separately counted; seller repairs use positive dated opens only.",
                      sources={str(p.relative_to(ROOT)):sha(p) for p in (Path(__file__),original_life_path,fixed_path,
                               STUDY/"native_account.py",candidate_path,listed.FEAR_FILE)})
        (HERE/"history_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    finally:
        formal.v14_policy = current_policy


if __name__ == "__main__":
    main()
