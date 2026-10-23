from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "im_put_grid_call_final_audit_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_SIDECAR = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "6a4b039368381f48c6f0a6614c7f5f644fd8988fd969838aef1da72f52b2ddfd"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"

V14 = ROOT / "outputs" / "im_mo_reconstructed_floor_selection_v14"
V17 = ROOT / "outputs" / "im_fixed_valuation_overlay_lower_boundary_scan_v17"
V18 = ROOT / "outputs" / "im_fixed_valuation_overlay_selected_put_sync_v18"
V27 = ROOT / "outputs" / "im_mo_call_daily_d10_threat_roll_v27"

V14_DAILY = V14 / "daily_candidates.csv.gz"
V17_GRID_TRADES = V17 / "overlay_trade_audit.csv"
V18_DAILY = V18 / "daily_candidates.csv.gz"
V27_DAILY = V27 / "daily_candidates.csv.gz"
V27_TRADES = V27 / "call_trades.csv"
V27_SIGNALS = V27 / "signals.csv"

GRID_SOURCE = "fixed_L0.85_H1.25__core_put_only"
CALL_SOURCE = "front_d10_iv26_daily_threat5_up5_next1_max5"

ROLL = "roll_im_no_put"
PUT = "core_put_floor3"
GRID = "core_put_grid_085_125"
CALL = "core_put_call_d10_threat5"
FULL = "full_put_grid_call"
CANDIDATES = (ROLL, PUT, GRID, CALL, FULL)

TRADING_DAYS = 252.0
MARGIN_PER_IM = 0.30
CASH_DAILY = 1.03 ** (1.0 / TRADING_DAYS) - 1.0
WINDOWS = {
    "full": None,
    "last_10y": pd.DateOffset(years=10),
    "last_5y": pd.DateOffset(years=5),
    "last_3y": pd.DateOffset(years=3),
    "last_1y": pd.DateOffset(years=1),
}

FROZEN_HASHES = {
    V14_DAILY: "c013e2ffdbe5435ae87601af319a3e263850e7d55f31e25fa3eee8a7ebb56614",
    V14 / "data_manifest.json": "d6caa2000d4706a3da1b3ad0c6f6207b56df428c0808b051012fb7d36c1c9212",
    V17_GRID_TRADES: "92d801768ded5229692fee5c2779415550262c732b910eebaf52b067d17806f2",
    V17 / "integrity_checks.json": "7a0e7093e6cb820c27f236d0eacd200f5d361acebac96b609d79f7f89f5f28da",
    V17 / "output_manifest.json": "ca76da29cf8970f70c11adf78f43ebd2d7e9650bf24a405b1b48be5f400c8e03",
    V18_DAILY: "6678d580ff17d3f8480e77f8e3f18e94d8ab2ebd543d3e02abf8d2fdead296ba",
    V18 / "integrity_checks.json": "48e5344323deef9ff89471ed53ce6f95a2b27f66c050b7707eb964a992167a67",
    V18 / "output_manifest.json": "c8adfbbdce57d6876654b61beaec778cb9caa10078df049ae7fe75678c386620",
    V27_DAILY: "f7cb51a1fe9885aba71f403f7f0ef2b5033c46c295caaa9953d767f82721b8b2",
    V27_TRADES: "005ed4a84ba48ddd011269e8e3e558701b043cd6e8d34d70f515b47d0c414285",
    V27_SIGNALS: "1bcfa7635a9d63f87a65eccf5f27eb93bbffbb579ef6ba3c6d03b7cb33bb2ec6",
    V27 / "execution_stats.json": "b5e121447637eef17948495ecdc1cd91472601c86e549c1fc2cce8599d025b94",
    V27 / "output_manifest.json": "86397fb5afa3b01f3f530aa63014f80d0233d2511ba1aabb58410e105399afa4",
    ROOT / "im_fixed_valuation_overlay_selected_put_sync_v18.py": "e449005c95b2b21e01af30bcc189e4e982ec28cbb9ff050bcc235475945131fc",
    ROOT / "im_mo_call_daily_d10_threat_roll_v27.py": "eb3df604cd2aec2c0a2cad6c5d6ff7fa1f85b5d27269abc8c05502878c3d7fc0",
    ROOT / "docs" / "im_mo_put_research_mainline_v1.md": "0caafc8a48518babd68108e067d3b61e4cda4694b7ac2b3c90dfda8718330738",
    ROOT / "docs" / "im_fixed_valuation_overlay_selected_put_sync_v18_spec.md": "830f35c9e245687868840872ea078d69921be1c5871b1b52521c2639a701e099",
    ROOT / "docs" / "im_mo_call_daily_d10_threat_roll_v27_spec.md": "38ab5360438c83b2d2a637d4c33dcdc2df02c001a5117bf9d15b64a469886c73",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_output_manifest(directory: Path) -> dict[str, Any]:
    manifest = json.loads((directory / "output_manifest.json").read_text(encoding="utf-8"))
    entries = manifest.get("files", manifest)
    errors: list[dict[str, str]] = []
    for name, meta in entries.items():
        path = directory / name
        actual = sha256(path) if path.exists() else "missing"
        expected = meta["sha256"] if isinstance(meta, dict) else meta
        if actual != expected:
            errors.append({"file": name, "expected": expected, "actual": actual})
    return {"directory": str(directory.relative_to(ROOT)), "files": len(entries), "errors": errors, "pass": not errors}


def verify_inputs() -> dict[str, Any]:
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError(f"Formal or staging output already exists: {OUTPUT}")
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Frozen final-audit specification hash mismatch")
    if SPEC_SIDECAR.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Frozen final-audit specification sidecar mismatch")
    mismatches = []
    for path, expected in FROZEN_HASHES.items():
        actual = sha256(path) if path.exists() else "missing"
        if actual != expected:
            mismatches.append({"path": str(path), "expected": expected, "actual": actual})
    manifests = [verify_output_manifest(path) for path in (V17, V18, V27)]
    if mismatches or not all(item["pass"] for item in manifests):
        raise RuntimeError(f"Frozen input verification failed: {mismatches}; manifests={manifests}")
    return {
        "spec_sha256": SPEC_SHA256,
        "frozen_input_count": len(FROZEN_HASHES),
        "frozen_hashes": {str(path.relative_to(ROOT)): value for path, value in FROZEN_HASHES.items()},
        "upstream_output_manifests": manifests,
    }


def load_sources() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    v14 = pd.read_csv(V14_DAILY, parse_dates=["date"])
    v18 = pd.read_csv(V18_DAILY, parse_dates=["date"])
    v27 = pd.read_csv(V27_DAILY, parse_dates=["date", "call_expiry"])
    grid_trades = pd.read_csv(V17_GRID_TRADES, parse_dates=["signal_date", "execution_date"])
    call_trades = pd.read_csv(
        V27_TRADES,
        parse_dates=["eval_date", "scheduled_execution_date", "actual_execution_date", "old_expiry", "new_expiry"],
    )
    call_signals = pd.read_csv(
        V27_SIGNALS,
        parse_dates=["eval_date", "scheduled_execution_date", "selection_expiry", "old_expiry"],
    )
    grid_trades = grid_trades[grid_trades["candidate"].eq("fixed_L0.85_H1.25")].copy()
    call_trades = call_trades[call_trades["candidate"].eq(CALL_SOURCE)].copy()
    call_signals = call_signals[call_signals["candidate"].eq(CALL_SOURCE)].copy()
    return v14, v18, v27, grid_trades, call_trades, call_signals


COMMON_COLUMNS = [
    "date", "layer", "candidate", "tri_close", "base_gross_ret", "overlay_gross_ret",
    "put_pnl_ret", "call_pnl_ret", "futures_cost_rate", "put_cost_rate", "call_cost_rate",
    "total_im_units", "put_mark_fraction", "put_fraction", "put_contract",
    "call_mark_fraction", "call_margin_fraction", "call_coverage", "call_delta", "call_contract",
    "call_strike", "call_expiry", "threat_roll_count", "threat_entry_blocked", "signal_value",
    "overlay_held_before", "overlay_held_eod", "overlay_buy", "overlay_sell", "roll_event",
    "cash_weight_raw", "cash_weight", "ret", "cash_ret", "nav", "drawdown",
]


def empty_position_fields(frame: pd.DataFrame) -> pd.DataFrame:
    frame["put_mark_fraction"] = 0.0
    frame["put_fraction"] = 0.0
    frame["put_contract"] = ""
    frame["call_mark_fraction"] = 0.0
    frame["call_margin_fraction"] = 0.0
    frame["call_coverage"] = 0.0
    frame["call_delta"] = np.nan
    frame["call_contract"] = ""
    frame["call_strike"] = np.nan
    frame["call_expiry"] = pd.NaT
    frame["threat_roll_count"] = 0
    frame["threat_entry_blocked"] = False
    return frame


def finish(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    frame = frame.copy()
    frame["candidate"] = label
    gross = frame["base_gross_ret"] + frame["overlay_gross_ret"] + frame["put_pnl_ret"] + frame["call_pnl_ret"]
    frame["ret"] = (
        (1.0 + gross)
        * (1.0 - frame["futures_cost_rate"])
        * (1.0 - frame["put_cost_rate"])
        * (1.0 - frame["call_cost_rate"])
        - 1.0
    )
    frame["cash_weight_raw"] = (
        1.0
        - MARGIN_PER_IM * frame["total_im_units"]
        - frame["put_mark_fraction"]
        - frame["call_margin_fraction"]
    )
    frame["cash_weight"] = frame["cash_weight_raw"].clip(lower=0.0)
    frame["cash_ret"] = frame["ret"] + frame["cash_weight"] * CASH_DAILY
    frame["nav"] = (1.0 + frame["cash_ret"]).cumprod()
    frame["drawdown"] = frame["nav"] / frame["nav"].cummax() - 1.0
    return frame[COMMON_COLUMNS]


def build_no_put(source: pd.DataFrame) -> pd.DataFrame:
    frame = source[["date", "layer", "tri_close", "gross_ret", "cost_rate"]].rename(
        columns={"gross_ret": "base_gross_ret", "cost_rate": "futures_cost_rate"}
    )
    frame["overlay_gross_ret"] = 0.0
    frame["put_pnl_ret"] = 0.0
    frame["call_pnl_ret"] = 0.0
    frame["put_cost_rate"] = 0.0
    frame["call_cost_rate"] = 0.0
    frame["total_im_units"] = 1.0
    frame = empty_position_fields(frame)
    frame["signal_value"] = np.nan
    frame["overlay_held_before"] = 0
    frame["overlay_held_eod"] = 0
    frame["overlay_buy"] = 0
    frame["overlay_sell"] = 0
    frame["roll_event"] = False
    return finish(frame, ROLL)


def build_from_v18(source: pd.DataFrame, label: str) -> pd.DataFrame:
    frame = source.copy().rename(
        columns={"gross_ret": "base_gross_ret", "call_pnl_ret": "source_call_pnl_ret"}
    )
    frame["call_pnl_ret"] = 0.0
    frame["call_cost_rate"] = 0.0
    frame["call_mark_fraction"] = 0.0
    frame["call_margin_fraction"] = 0.0
    frame["call_coverage"] = 0.0
    frame["call_delta"] = np.nan
    frame["call_contract"] = ""
    frame["call_strike"] = np.nan
    frame["call_expiry"] = pd.NaT
    frame["threat_roll_count"] = 0
    frame["threat_entry_blocked"] = False
    for column, default in [("signal_value", np.nan), ("overlay_held_before", 0), ("overlay_held_eod", 0), ("overlay_buy", 0), ("overlay_sell", 0), ("roll_event", False)]:
        if column not in frame:
            frame[column] = default
    return finish(frame, label)


def build_call(source: pd.DataFrame) -> pd.DataFrame:
    frame = source.copy().rename(columns={"gross_ret": "base_gross_ret", "cost_rate": "futures_cost_rate"})
    frame["overlay_gross_ret"] = 0.0
    frame["total_im_units"] = 1.0
    frame["signal_value"] = np.nan
    frame["overlay_held_before"] = 0
    frame["overlay_held_eod"] = 0
    frame["overlay_buy"] = 0
    frame["overlay_sell"] = 0
    frame["roll_event"] = False
    return finish(frame, CALL)


def build_full(grid: pd.DataFrame, call: pd.DataFrame) -> pd.DataFrame:
    call_fields = call[
        [
            "date", "layer", "call_pnl_ret", "call_cost_rate", "call_mark_fraction",
            "call_margin_fraction", "call_coverage", "call_delta", "call_contract", "call_strike",
            "call_expiry", "threat_roll_count", "threat_entry_blocked",
        ]
    ]
    frame = grid.drop(
        columns=[
            "candidate", "call_pnl_ret", "call_cost_rate", "call_mark_fraction", "call_margin_fraction",
            "call_coverage", "call_delta", "call_contract", "call_strike", "call_expiry",
            "threat_roll_count", "threat_entry_blocked", "cash_weight_raw", "cash_weight", "ret",
            "cash_ret", "nav", "drawdown",
        ]
    ).merge(call_fields, on=["date", "layer"], validate="one_to_one")
    return finish(frame, FULL)


def metrics(values: pd.Series) -> dict[str, float]:
    returns = values.astype(float).reset_index(drop=True)
    nav = pd.concat([pd.Series([1.0]), (1.0 + returns).cumprod()], ignore_index=True)
    total = float(nav.iloc[-1] - 1.0)
    ann = float(nav.iloc[-1] ** (TRADING_DAYS / len(returns)) - 1.0)
    vol = float(returns.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(returns) > 1 else np.nan
    std = float(returns.std(ddof=1)) if len(returns) > 1 else np.nan
    dd = nav / nav.cummax() - 1.0
    return {
        "total_return": total,
        "ann_return": ann,
        "ann_vol": vol,
        "sharpe_repo": float(returns.mean() / std * math.sqrt(TRADING_DAYS)) if np.isfinite(std) and std > 0 else np.nan,
        "max_dd": float(dd.min()),
        "calmar": ann / abs(float(dd.min())) if float(dd.min()) < 0 else np.nan,
    }


def metric_tables(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    formal: list[dict[str, Any]] = []
    annual: list[dict[str, Any]] = []
    drawdowns: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"], sort=True):
        group = group.sort_values("date").reset_index(drop=True)
        start, end = pd.Timestamp(group["date"].min()), pd.Timestamp(group["date"].max())
        for window, offset in WINDOWS.items():
            requested = start if offset is None else end - offset
            available = offset is None or start <= requested
            sample = group[group["date"].ge(requested)].reset_index(drop=True) if available else group.iloc[0:0]
            row: dict[str, Any] = {
                "layer": layer,
                "candidate": candidate,
                "window": window,
                "available": available,
                "requested_start": requested,
                "actual_start": sample["date"].min() if available else pd.NaT,
                "end": end,
                "rows": len(sample),
            }
            row.update(metrics(sample["cash_ret"]) if available else {key: np.nan for key in ["total_return", "ann_return", "ann_vol", "sharpe_repo", "max_dd", "calmar"]})
            if available:
                wealth = (1.0 + sample["cash_ret"]).cumprod()
                dd = wealth / wealth.cummax() - 1.0
                trough = int(dd.idxmin())
                peak = int(wealth.loc[:trough].idxmax())
                row["peak_date"] = sample.loc[peak, "date"]
                row["trough_date"] = sample.loc[trough, "date"]
                drawdowns.append({"layer": layer, "candidate": candidate, "window": window, "peak_date": row["peak_date"], "trough_date": row["trough_date"], "max_dd": row["max_dd"]})
            else:
                row["peak_date"] = pd.NaT
                row["trough_date"] = pd.NaT
            formal.append(row)
        for year, sample in group.groupby(group["date"].dt.year):
            annual.append({"layer": layer, "candidate": candidate, "year": int(year), **metrics(sample["cash_ret"])})
    return pd.DataFrame(formal), pd.DataFrame(annual), pd.DataFrame(drawdowns)


def attribution_table(formal: pd.DataFrame) -> pd.DataFrame:
    prior = {ROLL: None, PUT: ROLL, GRID: PUT, CALL: PUT, FULL: GRID}
    lookup = formal.set_index(["layer", "candidate", "window"])
    rows = []
    for layer in ("model", "real"):
        for candidate in CANDIDATES:
            for window in WINDOWS:
                item = lookup.loc[(layer, candidate, window)]
                base_name = prior[candidate]
                if base_name is None or not bool(item["available"]):
                    base_ann = base_dd = ann_delta = dd_improvement = np.nan
                else:
                    base = lookup.loc[(layer, base_name, window)]
                    if not bool(base["available"]):
                        base_ann = base_dd = ann_delta = dd_improvement = np.nan
                    else:
                        base_ann, base_dd = float(base["ann_return"]), float(base["max_dd"])
                        ann_delta = float(item["ann_return"] - base_ann)
                        dd_improvement = float(item["max_dd"] - base_dd)
                rows.append({"layer": layer, "candidate": candidate, "comparison_baseline": base_name or "", "window": window, "available": bool(item["available"]), "ann_return": item["ann_return"], "max_dd": item["max_dd"], "baseline_ann_return": base_ann, "baseline_max_dd": base_dd, "ann_return_delta_pp": ann_delta, "max_dd_improvement_pp": dd_improvement})
    return pd.DataFrame(rows)


def capital_table(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"], sort=True):
        group = group.sort_values("date").reset_index(drop=True)
        eod = MARGIN_PER_IM * group["total_im_units"] + group["put_mark_fraction"] + group["call_margin_fraction"]
        prior_put = group["put_mark_fraction"].shift(1).fillna(group["put_mark_fraction"])
        prior_call = group["call_margin_fraction"].shift(1).fillna(group["call_margin_fraction"])
        morning = MARGIN_PER_IM * group["total_im_units"] + prior_put + prior_call
        grid_open = group["overlay_buy"].astype(bool)
        rows.append(
            {
                "layer": layer,
                "candidate": candidate,
                "max_eod_capital_fraction": float(eod.max()),
                "eod_breach_days": int(eod.gt(1.0 + 1e-12).sum()),
                "max_morning_capital_proxy": float(morning.max()),
                "morning_breach_days": int(morning.gt(1.0 + 1e-12).sum()),
                "grid_open_days": int(grid_open.sum()),
                "max_grid_open_morning_capital_proxy": float(morning[grid_open].max()) if grid_open.any() else np.nan,
                "grid_open_breach_days": int((morning[grid_open] > 1.0 + 1e-12).sum()) if grid_open.any() else 0,
                "min_raw_cash_weight": float(group["cash_weight_raw"].min()),
                "max_total_im_units": float(group["total_im_units"].max()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "max_call_margin_fraction": float(group["call_margin_fraction"].max()),
                "max_call_coverage": float(group["call_coverage"].max()),
            }
        )
    return pd.DataFrame(rows)


def capital_breach_details(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (layer, candidate), group in daily.groupby(["layer", "candidate"], sort=True):
        group = group.sort_values("date").reset_index(drop=True)
        prior_put = group["put_mark_fraction"].shift(1).fillna(group["put_mark_fraction"])
        prior_call = group["call_margin_fraction"].shift(1).fillna(group["call_margin_fraction"])
        eod = MARGIN_PER_IM * group["total_im_units"] + group["put_mark_fraction"] + group["call_margin_fraction"]
        morning = MARGIN_PER_IM * group["total_im_units"] + prior_put + prior_call
        mask = eod.gt(1.0 + 1e-12) | morning.gt(1.0 + 1e-12)
        for index in group.index[mask]:
            item = group.loc[index]
            rows.append(
                {
                    "layer": layer,
                    "candidate": candidate,
                    "date": item["date"],
                    "overlay_buy": int(item["overlay_buy"]),
                    "total_im_units": float(item["total_im_units"]),
                    "im_margin_fraction": MARGIN_PER_IM * float(item["total_im_units"]),
                    "prior_put_mark_fraction": float(prior_put.loc[index]),
                    "prior_call_margin_fraction": float(prior_call.loc[index]),
                    "morning_capital_proxy": float(morning.loc[index]),
                    "eod_put_mark_fraction": float(item["put_mark_fraction"]),
                    "eod_call_margin_fraction": float(item["call_margin_fraction"]),
                    "eod_capital_fraction": float(eod.loc[index]),
                    "morning_breach": bool(morning.loc[index] > 1.0 + 1e-12),
                    "eod_breach": bool(eod.loc[index] > 1.0 + 1e-12),
                }
            )
    return pd.DataFrame(rows)


def event_table(grid_trades: pd.DataFrame, call_trades: pd.DataFrame, call_signals: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for layer in ("model", "real"):
        g = grid_trades[grid_trades["layer"].eq(layer)]
        t = call_trades[call_trades["layer"].eq(layer)]
        s = call_signals[call_signals["layer"].eq(layer)]
        rows.append(
            {
                "layer": layer,
                "grid_buys": int(g["action"].eq("buy").sum()),
                "grid_sells": int(g["action"].eq("sell").sum()),
                "grid_completed_cycles": int(min(g["action"].eq("buy").sum(), g["action"].eq("sell").sum())),
                "grid_causality_errors": int((g.loc[~g["execution_reason"].eq("initial_listing_carry"), "execution_date"] <= g.loc[~g["execution_reason"].eq("initial_listing_carry"), "signal_date"]).sum()),
                "real_grid_invalid_quotes": int((g["execution_open"].le(0) | g["execution_volume"].le(0)).sum()) if layer == "real" else 0,
                "call_opens": int(t["action"].eq("open").sum()),
                "call_rolls": int(t["action"].eq("roll").sum()),
                "call_closes": int(t["action"].eq("close").sum()),
                "threat_rolls": int(t["reason"].eq("threat_roll").sum()),
                "threat_stops": int(t["reason"].str.startswith("threat_stop").sum()),
                "call_causality_errors": int((t["actual_execution_date"] <= t["eval_date"]).sum()),
                "call_signals": len(s),
                "call_normal_iv_gate_errors": int((s.loc[~s["reason"].str.startswith("threat_") & s["contract"].fillna("").ne(""), "gate_pass"].astype(bool) != s.loc[~s["reason"].str.startswith("threat_") & s["contract"].fillna("").ne(""), "gate_iv"].ge(0.26 - 1e-12)).sum()),
                "threat_trigger_errors": int(s.loc[s["reason"].eq("threat_roll"), "threat_otm"].gt(0.05 + 1e-12).sum()),
            }
        )
    return pd.DataFrame(rows)


def parity_and_integrity(
    daily: pd.DataFrame,
    sources: dict[str, pd.DataFrame],
    capital: pd.DataFrame,
    events: pd.DataFrame,
    upstream: dict[str, Any],
) -> dict[str, Any]:
    errors: dict[str, float] = {}

    def compare(candidate: str, source: pd.DataFrame, mapping: dict[str, str]) -> float:
        target = daily[daily["candidate"].eq(candidate)]
        joined = target.merge(source, on=["layer", "date"], suffixes=("_new", "_source"), validate="one_to_one")
        values = []
        for new, old in mapping.items():
            values.append(float((joined[f"{new}_new"] - joined[f"{old}_source"]).abs().max()))
        return max(values) if values else 0.0

    errors["no_put_source_parity_max_abs"] = compare(
        ROLL, sources["no_put"], {"ret": "ret", "cash_ret": "cash_ret"}
    )
    errors["put_source_parity_max_abs"] = compare(
        PUT, sources["put"], {"ret": "ret", "cash_ret": "cash_ret"}
    )
    errors["grid_source_parity_max_abs"] = compare(
        GRID, sources["grid"], {"ret": "ret", "cash_ret": "cash_ret"}
    )
    errors["call_source_parity_max_abs"] = compare(
        CALL, sources["call"], {"ret": "ret", "cash_ret": "cash_ret"}
    )

    common = sources["put"][["layer", "date", "gross_ret", "cost_rate", "put_pnl_ret", "put_cost_rate", "put_mark_fraction"]].merge(
        sources["call"][["layer", "date", "gross_ret", "cost_rate", "put_pnl_ret", "put_cost_rate", "put_mark_fraction"]],
        on=["layer", "date"], suffixes=("_v18", "_v27"), validate="one_to_one",
    )
    common_diffs = []
    for column in ("gross_ret", "cost_rate", "put_pnl_ret", "put_cost_rate", "put_mark_fraction"):
        common_diffs.append(float((common[f"{column}_v18"] - common[f"{column}_v27"]).abs().max()))
    errors["v18_v27_common_base_max_abs"] = max(common_diffs)

    full = daily[daily["candidate"].eq(FULL)]
    grid = daily[daily["candidate"].eq(GRID)]
    call = daily[daily["candidate"].eq(CALL)]
    joined = full.merge(grid, on=["layer", "date"], suffixes=("_full", "_grid"), validate="one_to_one").merge(
        call[["layer", "date", "call_pnl_ret", "call_cost_rate", "call_margin_fraction", "call_coverage"]],
        on=["layer", "date"], validate="one_to_one",
    )
    component_diffs = []
    for column in ("base_gross_ret", "overlay_gross_ret", "put_pnl_ret", "futures_cost_rate", "put_cost_rate", "put_mark_fraction", "total_im_units"):
        component_diffs.append(float((joined[f"{column}_full"] - joined[f"{column}_grid"]).abs().max()))
    for column in ("call_pnl_ret", "call_cost_rate", "call_margin_fraction", "call_coverage"):
        component_diffs.append(float((joined[f"{column}_full"] - joined[column]).abs().max()))
    errors["full_component_isolation_max_abs"] = max(component_diffs)

    expected_ret = (
        (1.0 + daily["base_gross_ret"] + daily["overlay_gross_ret"] + daily["put_pnl_ret"] + daily["call_pnl_ret"])
        * (1.0 - daily["futures_cost_rate"])
        * (1.0 - daily["put_cost_rate"])
        * (1.0 - daily["call_cost_rate"])
        - 1.0
    )
    expected_cash = expected_ret + daily["cash_weight_raw"].clip(lower=0.0) * CASH_DAILY
    errors["return_identity_max_abs"] = float((daily["ret"] - expected_ret).abs().max())
    errors["cash_identity_max_abs"] = float((daily["cash_ret"] - expected_cash).abs().max())
    nav_expected = daily.groupby(["layer", "candidate"])["cash_ret"].transform(lambda x: (1.0 + x).cumprod())
    errors["nav_identity_max_abs"] = float((daily["nav"] - nav_expected).abs().max())

    v17_checks = json.loads((V17 / "integrity_checks.json").read_text(encoding="utf-8"))
    v18_checks = json.loads((V18 / "integrity_checks.json").read_text(encoding="utf-8"))
    v27_checks = json.loads((V27 / "audit_summary.json").read_text(encoding="utf-8"))
    execution_stats = json.loads((V27 / "execution_stats.json").read_text(encoding="utf-8"))
    checks = {
        **upstream,
        **errors,
        "rows": len(daily),
        "expected_rows": 5 * (2756 + 986),
        "candidate_count_per_layer": daily.groupby("layer")["candidate"].nunique().to_dict(),
        "duplicate_candidate_dates": int(daily.duplicated(["layer", "candidate", "date"]).sum()),
        "invalid_return_rows": int(daily[["ret", "cash_ret"]].isna().sum().sum() + daily[["ret", "cash_ret"]].le(-1.0).sum().sum()),
        "invalid_total_im_units": int((~daily["total_im_units"].isin([1.0, 2.0])).sum()),
        "put_scaled_with_grid_errors": int((full["put_fraction"].to_numpy() != grid["put_fraction"].to_numpy()).sum()),
        "call_scaled_with_grid_errors": int((full["call_coverage"].to_numpy() != call["call_coverage"].to_numpy()).sum()),
        "eod_capital_breach_rows": int(capital["eod_breach_days"].sum()),
        "morning_capital_breach_rows": int(capital["morning_breach_days"].sum()),
        "grid_open_capital_breach_rows": int(capital["grid_open_breach_days"].sum()),
        "event_causality_errors": int(events["grid_causality_errors"].sum() + events["call_causality_errors"].sum()),
        "event_quote_errors": int(events["real_grid_invalid_quotes"].sum()),
        "call_rule_errors": int(events["call_normal_iv_gate_errors"].sum() + events["threat_trigger_errors"].sum()),
        "v17_all_checks_passed": bool(v17_checks["all_checks_passed"]),
        "v18_all_checks_passed": bool(v18_checks["all_checks_passed"]),
        "v27_candidate_audit_passed": bool(v27_checks["candidate_audit"]["all_pass"]),
        "v27_final_pending": int(sum(item["final_pending"] for item in execution_stats.values())),
        "v27_scheduled_execution_failures": int(sum(item["scheduled_execution_failures"] for item in execution_stats.values())),
        "user_override_recorded": True,
    }
    tolerance_fields = [key for key in checks if key.endswith("_max_abs")]
    checks["all_checks_passed"] = bool(
        all(float(checks[key]) <= 1e-12 for key in tolerance_fields)
        and checks["rows"] == checks["expected_rows"]
        and checks["candidate_count_per_layer"] == {"model": 5, "real": 5}
        and all(checks[key] == 0 for key in [
            "duplicate_candidate_dates", "invalid_return_rows", "invalid_total_im_units",
            "put_scaled_with_grid_errors", "call_scaled_with_grid_errors", "eod_capital_breach_rows",
            "morning_capital_breach_rows", "grid_open_capital_breach_rows", "event_causality_errors",
            "event_quote_errors", "call_rule_errors", "v27_final_pending", "v27_scheduled_execution_failures",
        ])
        and checks["v17_all_checks_passed"]
        and checks["v18_all_checks_passed"]
        and checks["v27_candidate_audit_passed"]
        and all(item["pass"] for item in checks["upstream_output_manifests"])
    )
    return checks


def fmt(value: Any) -> str:
    return "N/A" if pd.isna(value) else f"{float(value):.2%}"


def record_text(
    formal: pd.DataFrame,
    attribution: pd.DataFrame,
    capital: pd.DataFrame,
    breaches: pd.DataFrame,
    events: pd.DataFrame,
    checks: dict[str, Any],
) -> str:
    lines = [
        "# IM Put + 固定估值网格 + 每日D10救援Call统一正式审计 v1",
        "",
        f"审计结论：`{'formal_research_audit_passed' if checks['all_checks_passed'] else 'formal_research_audit_failed'}`。这是研究审计证据，未批准实盘。",
        "",
        "## 用户覆盖",
        "",
        "用户明确接受5%救援真实全样本相对无救援线约1.13个百分点的CAGR损失，并选择其降低Call尾部Delta与保证金峰值的特征。v27原机械门槛失败保持不变；本版不把它改写成通过。",
        "",
        "## 统一路径",
        "",
        "- 固定底仓：1倍滚IM；Put为重建估值 + MOM120最低3张的3个月95%月换MO。",
        "- 网格：固定分数<=0.85新增1倍IM，>=1.25退出；新增仓不加Put。",
        "- Call：每日D10、合约IV>=26%，5%受威胁时向上5%并向后一到期月，最多5次；只覆盖固定底仓。",
        "- 网格T+1开盘，Put/Call T+1收盘；30%保证金/IM单位，剩余现金净年化3%。",
        "",
        "## 强制窗口",
        "",
        "|层|路径|Full CAGR / MaxDD|10Y|5Y|3Y|1Y|",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    labels = {ROLL: "裸滚IM", PUT: "主线Put", GRID: "Put+网格", CALL: "Put+Call救援", FULL: "Put+网格+Call救援"}
    lookup = formal.set_index(["layer", "candidate", "window"])
    for layer in ("model", "real"):
        for candidate in CANDIDATES:
            cells = []
            for window in WINDOWS:
                row = lookup.loc[(layer, candidate, window)]
                cells.append("N/A" if not bool(row["available"]) else f"{row['ann_return']:.2%} / {row['max_dd']:.2%}")
            lines.append(f"|{layer}|{labels[candidate]}|" + "|".join(cells) + "|")
    lines.extend([
        "",
        "## 资本与事件",
        "",
        "```text",
        capital.to_string(index=False),
        "```",
        "",
        "资本穿透明细：",
        "",
        "```text",
        breaches.to_string(index=False) if len(breaches) else "None",
        "```",
        "",
        "```text",
        events.to_string(index=False),
        "```",
        "",
        "## 完整性",
        "",
        f"- 逐日路径：{checks['rows']:,}行；5候选×2层；重复日期{checks['duplicate_candidate_dates']}。",
        f"- 上游/统一路径最大经济误差：{max(float(checks[key]) for key in checks if key.endswith('_max_abs')):.3e}。",
        f"- 日终/早盘代理/网格开仓资本穿透：{checks['eod_capital_breach_rows']}/{checks['morning_capital_breach_rows']}/{checks['grid_open_capital_breach_rows']}。",
        f"- 事件因果/报价/Call规则错误：{checks['event_causality_errors']}/{checks['event_quote_errors']}/{checks['call_rule_errors']}。",
        "",
        "## 实盘缺口",
        "",
        "真实IM/MO只有2022-07-22以来样本；模型层2015起的期权并非真实可交易。网格真实事件集中在2024且只有两轮。本回测使用官方开盘/收盘与结算，不含盘口价差、冲击、涨跌停无法成交、容量、动态保证金上调和税费。",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    upstream = verify_inputs()
    v14, v18, v27, grid_trades, call_trades, call_signals = load_sources()

    source_no_put = v14[v14["candidate"].eq("no_put")].sort_values(["layer", "date"]).reset_index(drop=True)
    source_put = v18[v18["candidate"].eq("base_core_put")].sort_values(["layer", "date"]).reset_index(drop=True)
    source_grid = v18[v18["candidate"].eq(GRID_SOURCE)].sort_values(["layer", "date"]).reset_index(drop=True)
    source_call = v27[v27["candidate"].eq(CALL_SOURCE)].sort_values(["layer", "date"]).reset_index(drop=True)

    daily_parts = []
    for layer in ("model", "real"):
        no_put_layer = source_no_put[source_no_put["layer"].eq(layer)].copy()
        put_layer = source_put[source_put["layer"].eq(layer)].copy()
        grid_layer = source_grid[source_grid["layer"].eq(layer)].copy()
        call_layer = source_call[source_call["layer"].eq(layer)].copy()
        roll = build_no_put(no_put_layer)
        put = build_from_v18(put_layer, PUT)
        grid = build_from_v18(grid_layer, GRID)
        call = build_call(call_layer)
        full = build_full(grid, call)
        daily_parts.extend([roll, put, grid, call, full])
    daily = pd.concat(daily_parts, ignore_index=True).sort_values(["layer", "candidate", "date"]).reset_index(drop=True)
    formal, annual, drawdowns = metric_tables(daily)
    attribution = attribution_table(formal)
    capital = capital_table(daily)
    breaches = capital_breach_details(daily)
    events = event_table(grid_trades, call_trades, call_signals)
    source_frames = {"no_put": source_no_put, "put": source_put, "grid": source_grid, "call": source_call}
    checks = parity_and_integrity(daily, source_frames, capital, events, upstream)

    override = {
        "date": "2026-08-19",
        "decision": "user_override_accept_daily_d10_threat5_as_research_mainline",
        "v27_original_decision": "keep_daily_d10_without_threat5",
        "accepted_tradeoff": {"real_full_cagr_lag_pp": 1.1284901189607899, "real_full_maxdd_improvement_pp": 1.34834062490268, "real_max_call_delta_improvement_pp": 17.83059650585882},
        "scope": "research mainline and final unified audit only",
        "live_approved": False,
    }
    audit_decision = "formal_research_audit_passed" if checks["all_checks_passed"] else "formal_research_audit_failed"
    decision = {
        "decision": audit_decision,
        "selected_research_suite": FULL if checks["all_checks_passed"] else None,
        "component_mainlines": {"put": "reconstructed_valmom_floor3", "grid": "fixed_L0.85_H1.25__core_put_only", "call": CALL_SOURCE},
        "user_override": override["decision"],
        "blocking_issue": None if checks["all_checks_passed"] else "model_intraday_capital_breach_on_grid_entry",
        "live_approved": False,
        "research_status": "RESEARCH_ONLY_NOT_LIVE_APPROVED",
    }

    STAGING.mkdir(parents=True)
    daily.to_csv(STAGING / "daily_candidates.csv.gz", index=False, compression="gzip")
    formal.to_csv(STAGING / "metrics_by_window.csv", index=False)
    annual.to_csv(STAGING / "annual_metrics.csv", index=False)
    attribution.to_csv(STAGING / "layer_attribution.csv", index=False)
    drawdowns.to_csv(STAGING / "drawdown_audit.csv", index=False)
    capital.to_csv(STAGING / "capital_audit.csv", index=False)
    breaches.to_csv(STAGING / "capital_breach_details.csv", index=False)
    events.to_csv(STAGING / "event_audit.csv", index=False)
    grid_trades.to_csv(STAGING / "grid_events.csv", index=False)
    call_trades.to_csv(STAGING / "call_events.csv", index=False)
    call_signals.to_csv(STAGING / "call_signals.csv.gz", index=False, compression="gzip")
    (STAGING / "integrity_checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (STAGING / "user_override.json").write_text(json.dumps(override, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "decision_summary.json").write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
    (STAGING / "record.md").write_text(record_text(formal, attribution, capital, breaches, events, checks), encoding="utf-8")
    (STAGING / "command_log.txt").write_text(f"python {Path(__file__).name}\npython -m py_compile {Path(__file__).name}\n", encoding="utf-8")
    manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "sources": upstream,
        "samples": {"model": ["2015-04-16", "2026-08-14"], "real": ["2022-07-22", "2026-08-14"]},
        "data": {"real": "CFFEX official IM/MO open-close-settlement-volume-open-interest", "model": "frozen model IM/theoretical MO path", "direction_index": "CSI 1000 price index", "mom120": "CSI 1000 total return index only"},
        "execution": {"grid": "T close signal / T+1 active IM official open", "put_call": "T close signal / T+1 official close", "settlement": "official settlement", "im_margin_buffer_per_unit": MARGIN_PER_IM, "cash_annual": 0.03},
        "frictions": {"inherited": "frozen IM/Put costs and Call 1bp each side", "excluded": ["bid-ask spread", "opening/closing impact", "price-limit non-fill", "order-book capacity", "dynamic margin hike", "tax"]},
        "decision": decision,
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True).stdout.strip(),
        "git_status": subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True).stdout.strip(),
    }
    (STAGING / "data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    output_manifest = {
        "version": VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": {path.name: {"sha256": sha256(path), "bytes": path.stat().st_size} for path in sorted(STAGING.iterdir()) if path.is_file()},
    }
    (STAGING / "output_manifest.json").write_text(json.dumps(output_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    STAGING.replace(OUTPUT)
    print(json.dumps({"decision": decision, "integrity": checks, "capital": capital.to_dict("records"), "events": events.to_dict("records")}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
