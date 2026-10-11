"""Summarize negative-MOM120 full account and true core-only account by year."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from run_core_only import HERE, ROOT

STUDY = ROOT / "quant_param_scan_runs/20260926_icim_fix8_momentum_transfer_robustness"
PAIRED = ROOT / "quant_param_scan_runs/20260928_fear_entry_paired_exit_fix9_certified_v2"
FIX11 = ROOT / "quant_research_runs/20261002_fix10_vs_fix11_full_history"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def full_real() -> pd.DataFrame:
    ic = pd.read_csv(FIX11 / "account_daily_paths.csv.gz")
    ic = ic.loc[ic.arm.eq("FIX11_full") & ic["product"].eq("IC")].copy()
    im = pd.read_csv(PAIRED / "outputs/fix9_full_account_daily_nav.csv.gz")
    im = im.loc[im.candidate.eq("IM_OR_ENTRY_PAIRED_FEAR_EXIT_50_LISTED_FIX9")].copy()
    frames = []
    for product, daily in (("IC", ic), ("IM", im)):
        risk = pd.read_csv((FIX11 / "account_inputs/FIX11_full/native_fix4_ic_risk_signals_v1.csv.gz")
                           if product == "IC" else
                           (STUDY / "input_variants/im/formal/native_fix4_im_risk_signals_v1.csv.gz"))
        signal = risk[["signal_date", "execution_date", "momentum_120"]].dropna(subset=["execution_date"])
        daily = daily.merge(signal, left_on="date", right_on="execution_date", how="left", validate="one_to_one")
        daily["mom120_negative_execution"] = daily.momentum_120.lt(0)
        daily["data_layer"] = "real"
        frames.append(daily)
    return pd.concat(frames, ignore_index=True)


def summarize(frame: pd.DataFrame, *, layer: str, product: str,
              scenario: str, year: int | None) -> dict:
    r = frame.return_net.to_numpy(float)
    if not len(r) or not np.isfinite(r).all() or (r <= -1).any():
        raise RuntimeError(f"invalid returns: {layer}/{product}/{scenario}/{year}")
    return {"data_layer": layer, "product": product, "scenario": scenario,
            "year": year if year is not None else "ALL",
            "start": str(frame.date.iloc[0]), "end": str(frame.date.iloc[-1]),
            "observations": len(frame), "positive_days": int((r > 0).sum()),
            "negative_days": int((r < 0).sum()), "win_day_fraction": float((r > 0).mean()),
            "net_compound_return": float(np.prod(1 + r) - 1)}


def main() -> None:
    sources = {
        "model": (pd.read_csv(HERE / "model_latest_full_daily.csv.gz"),
                  pd.read_csv(HERE / "model_core_only_daily.csv.gz"),
                  pd.read_csv(HERE / "model_no_grid_daily.csv.gz"),
                  pd.read_csv(HERE / "model_pure_roll_daily.csv.gz")),
        "real": (full_real(), pd.read_csv(HERE / "real_core_only_daily.csv.gz"),
                 pd.read_csv(HERE / "real_no_grid_daily.csv.gz"),
                 pd.read_csv(HERE / "real_pure_roll_daily.csv.gz")),
    }
    rows, checks = [], {}
    for layer, (full, core, no_grid, pure_roll) in sources.items():
        for product in ("IC", "IM"):
            f = full.loc[full["product"].eq(product)].sort_values("date").reset_index(drop=True)
            c = core.loc[core["product"].eq(product)].sort_values("date").reset_index(drop=True)
            n = no_grid.loc[no_grid["product"].eq(product)].sort_values("date").reset_index(drop=True)
            p = pure_roll.loc[pure_roll["product"].eq(product)].sort_values("date").reset_index(drop=True)
            if f.date.tolist() != c.date.tolist() or f.date.tolist() != n.date.tolist() or f.date.tolist() != p.date.tolist():
                raise RuntimeError(f"full/core/no-grid/pure-roll calendar mismatch: {layer}/{product}")
            # Signal T governs the earnings row on its designated execution
            # date T+1; never classify T's preceding P&L using T-close data.
            if not f.mom120_negative_execution.fillna(False).equals(c.mom120_negative_execution.fillna(False)):
                raise RuntimeError(f"MOM120 signal mismatch: {layer}/{product}")
            f["year"] = pd.to_datetime(f.date).dt.year
            c["year"] = pd.to_datetime(c.date).dt.year
            n["year"] = pd.to_datetime(n.date).dt.year
            p["year"] = pd.to_datetime(p.date).dt.year
            negative_mask = f.mom120_negative_execution.fillna(False).to_numpy(dtype=bool)
            negative = f.loc[negative_mask].copy()
            negative_no_grid = n.loc[negative_mask].copy()
            negative_core = c.loc[negative_mask].copy()
            if negative.empty:
                raise RuntimeError(f"no negative MOM120 rows: {layer}/{product}")
            if not (negative.date.tolist() == negative_no_grid.date.tolist() == negative_core.date.tolist()):
                raise RuntimeError(f"conditional calendar mismatch: {layer}/{product}")
            checks[f"{layer}_{product}"] = {
                "full_rows": len(f), "core_rows": len(c),
                "negative_mom120_rows": len(negative),
                "negative_no_grid_rows": len(negative_no_grid),
                "negative_core_rows": len(negative_core),
                "negative_mom120_source": "prior T close, mapped to T+1 execution/P&L row",
                "full_nav_identity_error": float(np.max(np.abs(np.cumprod(1 + f.return_net.to_numpy(float)) - f.nav.to_numpy(float)))),
                "core_nav_identity_error": float(np.max(np.abs(np.cumprod(1 + c.return_net.to_numpy(float)) - c.nav.to_numpy(float)))),
                "no_grid_nav_identity_error": float(np.max(np.abs(np.cumprod(1 + n.return_net.to_numpy(float)) - n.nav.to_numpy(float)))),
                "pure_roll_nav_identity_error": float(np.max(np.abs(np.cumprod(1 + p.return_net.to_numpy(float)) - p.nav.to_numpy(float)))),
            }
            for scenario, data in (("full_strategy", f), ("core_plus_momentum_no_grid", n),
                                   ("mom120_negative_full", negative),
                                   ("mom120_negative_no_grid", negative_no_grid),
                                   ("mom120_negative_core_only", negative_core),
                                   ("core_only", c),
                                   ("pure_1x_roll", p)):
                for year, group in data.groupby("year", sort=True):
                    rows.append(summarize(group, layer=layer, product=product, scenario=scenario, year=int(year)))
                rows.append(summarize(data, layer=layer, product=product, scenario=scenario, year=None))
    summary = pd.DataFrame(rows)
    summary.to_csv(HERE / "annual_and_overall.csv", index=False, encoding="utf-8-sig")
    (HERE / "summary_audit.json").write_text(json.dumps({
        "status": "PASS" if all(max(v["full_nav_identity_error"], v["core_nav_identity_error"],
                                    v["no_grid_nav_identity_error"], v["pure_roll_nav_identity_error"]) < 2e-9 for v in checks.values()) else "FAIL",
        "checks": checks,
        "sources": {str(p.relative_to(ROOT)): sha(p) for p in (
            FIX11 / "account_daily_paths.csv.gz",
            PAIRED / "outputs/fix9_full_account_daily_nav.csv.gz",
            HERE / "model_latest_full_daily.csv.gz",
            HERE / "model_core_only_daily.csv.gz",
            HERE / "real_core_only_daily.csv.gz",
            HERE / "model_no_grid_daily.csv.gz",
            HERE / "real_no_grid_daily.csv.gz",
            HERE / "model_pure_roll_daily.csv.gz",
            HERE / "real_pure_roll_daily.csv.gz")},
        "scope": "research-only as-if-current; model and real histories never chained into one account NAV",
        "return_definition": "within-year or within-segment compound net daily returns; all three MOM120 scenarios use the same prior-T-close-selected T+1 dates and omit other dates",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(summary.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
