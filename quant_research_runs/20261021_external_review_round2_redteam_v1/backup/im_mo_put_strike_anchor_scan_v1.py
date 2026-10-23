from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

import im_mainline_v1_1 as parent
import im_mo_close_execution_v8 as v8
import im_mo_csi1000_put_protection_battery_v6 as v6
import im_mo_front95_fixed_dynamic_momentum_validation_v5 as v5
import im_valuation_frequency_tenor_scan_v4 as v4


ROOT = Path(__file__).resolve().parent
VERSION = "im_mo_put_strike_anchor_scan_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "8118765709acec14af860f9ff61e8a9a4a7bc5e951f24109f830441de11d8303"
OUTPUT = ROOT / "outputs" / VERSION
SCAN = (
    ROOT
    / "quant_param_scan_runs"
    / "20260823_ic_im_im_v1_2_strike_anchor_diagnostic_v1_im_core_mo_put_strike_reference_asset_x_moneyness"
)

V8_DAILY = ROOT / "outputs" / "im_mo_close_execution_v8" / "daily_candidates.csv.gz"
IM12_BASE = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_put_v4" / "daily_nav.csv.gz"
IM50_REAL = ROOT / "outputs" / "im_roll50_momentum50_v1" / "daily_nav.csv"

ANCHORS = ("active_im", "csi1000_spot", "matched_expiry_im")
MONEYNESS = (0.90, 0.95, 1.00)
WINDOWS: tuple[tuple[str, pd.DateOffset | None], ...] = (
    ("full", None),
    ("last_10y", pd.DateOffset(years=10)),
    ("last_5y", pd.DateOffset(years=5)),
    ("last_3y", pd.DateOffset(years=3)),
    ("last_1y", pd.DateOffset(years=1)),
)
PRIMARY_SCOPE = "im12_core_put"
CORE_SCOPE = "core_1x"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip()


def candidate_name(anchor: str, moneyness: float) -> str:
    return f"{anchor}_m{int(round(moneyness * 100)):03d}"


def verify_preregistered_inputs() -> dict[str, str]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Preregistered specification hash mismatch")
    sidecar = SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower()
    if sidecar != SPEC_SHA256:
        raise RuntimeError("Specification SHA sidecar mismatch")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output exists and cannot be overwritten: {OUTPUT}")
    if not SCAN.exists():
        raise FileNotFoundError(f"Initialized scan folder missing: {SCAN}")
    required = [
        Path(v8.__file__),
        Path(v6.__file__),
        Path(v5.__file__),
        Path(v4.__file__),
        Path(parent.__file__),
        Path(v5.IM_QUOTES),
        Path(v4.OPTIONS),
        Path(v4.PRICE),
        Path(v4.UPSTREAM),
        V8_DAILY,
        IM12_BASE,
        IM50_REAL,
    ]
    missing = [str(path) for path in required if not Path(path).exists()]
    if missing:
        raise FileNotFoundError(f"Required inputs missing: {missing}")
    return {str(path.relative_to(ROOT)): sha256(path) for path in required}


def load_research_inputs() -> dict[str, Any]:
    upstream, _, _, _, _, raw_options = v4.load_inputs()
    upstream = upstream.sort_values("date").reset_index(drop=True)
    expiry_map = v4.actual_expiry_map(raw_options, upstream)
    options = v4.prepare_options(raw_options, expiry_map)
    im_quotes = pd.read_csv(v5.IM_QUOTES, parse_dates=["date"])
    active_im = v8.active_im_closes(upstream).sort_values("date").reset_index(drop=True)
    spot = pd.read_csv(v4.PRICE, parse_dates=["date"])[["date", "close"]]
    spot = (
        upstream[["date"]]
        .merge(spot, on="date", how="left", validate="one_to_one")
        .sort_values("date")
        .reset_index(drop=True)
    )
    if spot["close"].isna().any() or spot["close"].le(0).any():
        raise RuntimeError("Missing CSI1000 spot close on real IM/MO dates")

    parent_schedule, parent_audit = parent.load_authoritative_local_state()
    parent_schedule = parent_schedule.sort_values("date").reset_index(drop=True)
    parent_schedule["prior_date"] = parent_schedule["date"].shift(1)
    real_schedule = parent_schedule[
        parent_schedule["date"].isin(pd.DatetimeIndex(upstream["date"]))
    ].copy()
    real_schedule = real_schedule.rename(
        columns={"date": "execution_date", "prior_date": "eval_date"}
    )
    real_schedule["binary_target_qty"] = real_schedule[
        "put_execution_target_qty"
    ].astype(int)
    real_schedule["target_qty"] = real_schedule["binary_target_qty"]
    if len(real_schedule) != len(upstream):
        raise RuntimeError("Parent v1.1 schedule does not align one-to-one with real IM dates")
    if real_schedule["eval_date"].isna().any() or (
        real_schedule["execution_date"] <= real_schedule["eval_date"]
    ).any():
        raise RuntimeError("Invalid T/T+1 schedule for v1.1 Put targets")

    im12_base = pd.read_csv(IM12_BASE, parse_dates=["date"], low_memory=False)
    im12_base = (
        upstream[["date"]]
        .merge(
            im12_base[
                [
                    "date",
                    "baseline_pre_cash_ret",
                    "blend_cash_weight",
                    "no_put_ret",
                    "total_im_units",
                    "momentum_weight",
                ]
            ],
            on="date",
            how="left",
            validate="one_to_one",
        )
        .sort_values("date")
        .reset_index(drop=True)
    )
    if im12_base.isna().any().any():
        raise RuntimeError("Missing IM 1.2 50:50 base fields")

    return {
        "upstream": upstream,
        "raw_options": raw_options,
        "options": options,
        "im_quotes": im_quotes,
        "active_im": active_im,
        "spot": spot,
        "schedule": real_schedule,
        "parent_audit": parent_audit,
        "im12_base": im12_base,
    }


def reference_price(
    anchor: str,
    day: pd.Timestamp,
    target_month: pd.Timestamp,
    *,
    active_lookup: pd.Series,
    spot_lookup: pd.Series,
    im_quote_lookup: pd.DataFrame,
) -> float | None:
    if pd.isna(target_month):
        return None
    if anchor == "active_im":
        value = active_lookup.get(day, np.nan)
        return None if pd.isna(value) or float(value) <= 0 else float(value)
    if anchor == "csi1000_spot":
        value = spot_lookup.get(day, np.nan)
        return None if pd.isna(value) or float(value) <= 0 else float(value)
    if anchor == "matched_expiry_im":
        contract = f"IM{pd.Timestamp(target_month):%y%m}"
        try:
            row = im_quote_lookup.loc[(contract, day)]
        except KeyError:
            return None
        if isinstance(row, pd.DataFrame):
            raise RuntimeError(f"Duplicate matched-expiry IM quote: {contract} {day.date()}")
        required = [row.get("close", np.nan), row.get("volume", np.nan), row.get("open_interest", np.nan)]
        if any(pd.isna(value) or float(value) <= 0 for value in required):
            return None
        return float(row["close"])
    raise ValueError(anchor)


def select_by_reference(
    options: pd.DataFrame,
    day: pd.Timestamp,
    month: pd.Timestamp,
    target: float,
    reference: float | None,
) -> pd.Series | None:
    if reference is None or reference <= 0:
        return None
    chain = options[
        options["date"].eq(day) & options["contract_month"].eq(pd.Timestamp(month))
    ].copy()
    liquid = chain[
        chain["close"].notna()
        & chain["close"].gt(0)
        & chain["volume"].gt(0)
        & chain["open_interest"].gt(0)
    ].copy()
    if liquid.empty:
        return None
    liquid["entry_moneyness"] = liquid["strike"] / reference
    liquid["target_error"] = (liquid["entry_moneyness"] - target).abs().round(12)
    selected = liquid.sort_values(["target_error", "strike", "contract"]).iloc[0].copy()
    selected["reference_price"] = reference
    return selected


def run_anchor_candidate(
    anchor: str,
    moneyness: float,
    inputs: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    active_lookup = inputs["active_im"].set_index("date")["close"]
    spot_lookup = inputs["spot"].set_index("date")["close"]
    quote_lookup = inputs["im_quotes"].set_index(["contract", "date"])
    original_selector = v8.select_close_contract

    def selector(
        options: pd.DataFrame,
        ignored_reference: pd.Series,
        day: pd.Timestamp,
        month: pd.Timestamp,
        target: float,
    ) -> pd.Series | None:
        del ignored_reference
        reference = reference_price(
            anchor,
            pd.Timestamp(day),
            pd.Timestamp(month),
            active_lookup=active_lookup,
            spot_lookup=spot_lookup,
            im_quote_lookup=quote_lookup,
        )
        return select_by_reference(options, pd.Timestamp(day), pd.Timestamp(month), target, reference)

    label = candidate_name(anchor, moneyness)
    v8.select_close_contract = selector
    try:
        overlay, trades, lives = v8.run_real_normal_close(
            inputs["upstream"],
            inputs["options"],
            inputs["active_im"],
            inputs["schedule"],
            "3m",
            moneyness,
            label,
        )
    finally:
        v8.select_close_contract = original_selector

    if not trades.empty:
        references: list[float] = []
        moneyness_values: list[float] = []
        for row in trades.itertuples(index=False):
            month = (
                pd.NaT
                if pd.isna(row.desired_contract_month)
                else pd.Timestamp(row.desired_contract_month)
            )
            value = reference_price(
                anchor,
                pd.Timestamp(row.actual_execution_date),
                month,
                active_lookup=active_lookup,
                spot_lookup=spot_lookup,
                im_quote_lookup=quote_lookup,
            )
            references.append(np.nan if value is None else value)
            moneyness_values.append(
                np.nan
                if value is None or pd.isna(row.new_strike)
                else float(row.new_strike) / value
            )
        trades = trades.copy()
        trades["anchor"] = anchor
        trades["target_moneyness"] = moneyness
        trades["reference_price"] = references
        trades["entry_moneyness"] = moneyness_values
    overlay = overlay.copy()
    overlay["anchor"] = anchor
    overlay["target_moneyness"] = moneyness
    return overlay, trades, lives


def official_active_reference(inputs: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    overlay, trades, _ = v8.run_real_normal_close(
        inputs["upstream"],
        inputs["options"],
        inputs["active_im"],
        inputs["schedule"],
        "3m",
        0.95,
        "official_active_reference",
    )
    return overlay, trades


def build_paths(
    inputs: dict[str, Any], overlays: dict[str, pd.DataFrame]
) -> tuple[pd.DataFrame, dict[str, float]]:
    upstream = inputs["upstream"]
    real_base = upstream[["date", "im_gross_ret", "cost_rate", "im_net_ret"]].rename(
        columns={"im_gross_ret": "gross_ret", "im_net_ret": "net_ret"}
    )
    core = v6.assemble_layer("real", real_base, overlays)
    core["scope"] = CORE_SCOPE
    core["strategy_ret"] = core["cash_ret"]

    im12_parts: list[pd.DataFrame] = []
    no = inputs["im12_base"].copy()
    no["candidate"] = "no_put"
    no["put_pnl_ret"] = 0.0
    no["put_cost_rate"] = 0.0
    no["put_mark_fraction"] = 0.0
    no["put_fraction"] = 0.0
    no["put_contract"] = ""
    no["strategy_ret"] = no["no_put_ret"]
    im12_parts.append(no)
    for label, overlay in overlays.items():
        frame = inputs["im12_base"].merge(
            overlay[
                [
                    "date",
                    "put_pnl_ret",
                    "put_cost_rate",
                    "put_mark_fraction",
                    "put_fraction",
                    "put_contract",
                ]
            ],
            on="date",
            how="left",
            validate="one_to_one",
        )
        scale = 0.5
        frame["put_pnl_ret"] *= scale
        frame["put_cost_rate"] *= scale
        frame["put_mark_fraction"] *= scale
        frame["put_fraction"] *= scale
        frame["candidate"] = label
        frame["pre_cash_ret"] = (
            1.0 + frame["baseline_pre_cash_ret"] + frame["put_pnl_ret"]
        ) * (1.0 - frame["put_cost_rate"]) - 1.0
        frame["cash_weight"] = (
            frame["blend_cash_weight"] - frame["put_mark_fraction"]
        )
        if frame["cash_weight"].lt(-1e-12).any():
            raise RuntimeError(f"Negative standardized cash for {label}")
        frame["cash_weight"] = frame["cash_weight"].clip(lower=0.0)
        frame["strategy_ret"] = (
            frame["pre_cash_ret"] + frame["cash_weight"] * v6.CASH_DAILY
        )
        im12_parts.append(frame)
    im12 = pd.concat(im12_parts, ignore_index=True, sort=False)
    im12["scope"] = PRIMARY_SCOPE

    keep = [
        "date",
        "scope",
        "candidate",
        "strategy_ret",
        "put_pnl_ret",
        "put_cost_rate",
        "put_mark_fraction",
        "put_fraction",
        "put_contract",
    ]
    daily = pd.concat([core[keep], im12[keep]], ignore_index=True, sort=False)
    daily = daily.sort_values(["scope", "candidate", "date"]).reset_index(drop=True)
    daily["nav"] = daily.groupby(["scope", "candidate"])["strategy_ret"].transform(
        lambda values: (1.0 + values).cumprod()
    )
    daily["drawdown"] = daily["nav"] / daily.groupby(
        ["scope", "candidate"]
    )["nav"].cummax() - 1.0

    frozen = pd.read_csv(V8_DAILY, parse_dates=["date"])
    frozen_no = frozen[
        frozen["layer"].eq("real") & frozen["candidate"].eq("no_put")
    ].sort_values("date")
    core_no = core[core["candidate"].eq("no_put")].sort_values("date")
    core_no_put_parity = float(
        np.abs(core_no["strategy_ret"].to_numpy() - frozen_no["cash_ret"].to_numpy()).max()
    )

    real_50 = pd.read_csv(IM50_REAL, parse_dates=["date"])[["date", "blend_ret"]]
    blend_join = inputs["im12_base"][["date", "no_put_ret"]].merge(
        real_50, on="date", how="inner", validate="one_to_one"
    )
    im12_no_put_parity = float((blend_join["no_put_ret"] - blend_join["blend_ret"]).abs().max())
    if core_no_put_parity > 1e-14 or im12_no_put_parity > 1e-14:
        raise RuntimeError(
            f"No-Put parity failure: core={core_no_put_parity}, im12={im12_no_put_parity}"
        )
    return daily, {
        "core_no_put_parity_max_abs": core_no_put_parity,
        "im12_no_put_parity_max_abs": im12_no_put_parity,
    }


def active_engine_parity(
    official: pd.DataFrame, candidate: pd.DataFrame
) -> float:
    columns = ["put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"]
    joined = official[["date", *columns]].merge(
        candidate[["date", *columns]],
        on="date",
        suffixes=("_official", "_candidate"),
        validate="one_to_one",
    )
    return float(
        max(
            (joined[f"{column}_official"] - joined[f"{column}_candidate"]).abs().max()
            for column in columns
        )
    )


def candidate_parameters(label: str) -> tuple[str, float]:
    if label == "no_put":
        return "none", 0.0
    anchor, m = label.rsplit("_m", 1)
    return anchor, int(m) / 100.0


def metric_tables(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    wide_rows: list[dict[str, Any]] = []
    for (scope, label), group in daily.groupby(["scope", "candidate"], sort=True):
        group = group.sort_values("date")
        start, end = pd.Timestamp(group["date"].min()), pd.Timestamp(group["date"].max())
        anchor, moneyness = candidate_parameters(label)
        candidate_id = f"{scope}_{label}"
        wide: dict[str, Any] = {
            "candidate": candidate_id,
            "scope": scope,
            "base_candidate": label,
            "anchor": anchor,
            "moneyness": moneyness,
        }
        for window, offset in WINDOWS:
            requested = start if offset is None else end - offset
            available = offset is None or start <= requested
            sample = group[group["date"].ge(requested)] if available else group
            values = v6.metrics(sample["strategy_ret"])
            row = {
                "candidate": candidate_id,
                "segment": window,
                "start": sample["date"].min().date().isoformat(),
                "end": sample["date"].max().date().isoformat(),
                "rows": int(len(sample)),
                "ann_return": values["ann_return"],
                "ann_vol": values["ann_vol"],
                "sharpe_repo": values["sharpe_repo"],
                "max_dd": values["max_dd"],
                "scope": scope,
                "base_candidate": label,
                "anchor": anchor,
                "moneyness": moneyness,
                "put_cost_total": float(sample["put_cost_rate"].sum()),
                "avg_put_mark_fraction": float(sample["put_mark_fraction"].mean()),
                "put_holding_day_ratio": float(sample["put_fraction"].gt(0).mean()),
                "requested_window_available": bool(available),
                "clipped_to_available_history": bool(not available),
            }
            rows.append(row)
            wide[f"ann_return_{window}"] = values["ann_return"]
            wide[f"max_dd_{window}"] = values["max_dd"]
            wide[f"sharpe_repo_{window}"] = values["sharpe_repo"]
            wide[f"available_{window}"] = bool(available)
        wide_rows.append(wide)
    return pd.DataFrame(rows), pd.DataFrame(wide_rows)


def selection_audit(
    trades: pd.DataFrame, inputs: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    active_lookup = inputs["active_im"].set_index("date")["close"]
    spot_lookup = inputs["spot"].set_index("date")["close"]
    quote_lookup = inputs["im_quotes"].set_index(["contract", "date"])
    opening = trades[trades["action"].isin(["close_buy", "close_roll"])].copy()
    rows: list[dict[str, Any]] = []
    for row in opening.itertuples(index=False):
        day = pd.Timestamp(row.actual_execution_date)
        month = pd.Timestamp(row.desired_contract_month)
        reference = reference_price(
            str(row.anchor),
            day,
            month,
            active_lookup=active_lookup,
            spot_lookup=spot_lookup,
            im_quote_lookup=quote_lookup,
        )
        expected = select_by_reference(
            inputs["options"], day, month, float(row.target_moneyness), reference
        )
        expected_contract = "" if expected is None else str(expected["contract"])
        rows.append(
            {
                "candidate": row.candidate,
                "anchor": row.anchor,
                "target_moneyness": row.target_moneyness,
                "signal_eval_date": row.signal_eval_date,
                "actual_execution_date": day,
                "desired_contract_month": month,
                "reference_price": reference,
                "selected_contract": row.new_contract,
                "expected_contract": expected_contract,
                "selected_strike": row.new_strike,
                "actual_moneyness": np.nan
                if reference is None
                else float(row.new_strike) / reference,
                "target_error": np.nan
                if reference is None
                else abs(float(row.new_strike) / reference - float(row.target_moneyness)),
                "nearest_contract_match": str(row.new_contract) == expected_contract,
                "delay_calendar_days": (
                    day - pd.Timestamp(row.scheduled_execution_date)
                ).days,
            }
        )
    audit = pd.DataFrame(rows)
    if audit.empty or not audit["nearest_contract_match"].all():
        raise RuntimeError("Strike-anchor contract selection audit failed")
    summary = {
        "opening_or_roll_trades": int(len(audit)),
        "nearest_contract_match_rate": float(audit["nearest_contract_match"].mean()),
        "max_target_error": float(audit["target_error"].max()),
        "max_delay_calendar_days": int(audit["delay_calendar_days"].max()),
    }
    return audit, summary


def basis_diagnostics(trades: pd.DataFrame, inputs: dict[str, Any]) -> pd.DataFrame:
    active_lookup = inputs["active_im"].set_index("date")["close"]
    spot_lookup = inputs["spot"].set_index("date")["close"]
    quote_lookup = inputs["im_quotes"].set_index(["contract", "date"])
    events = trades[
        trades["candidate"].eq("active_im_m095")
        & trades["action"].isin(["close_buy", "close_roll"])
    ].copy()
    rows: list[dict[str, Any]] = []
    for row in events.itertuples(index=False):
        day = pd.Timestamp(row.actual_execution_date)
        month = pd.Timestamp(row.desired_contract_month)
        active = reference_price(
            "active_im",
            day,
            month,
            active_lookup=active_lookup,
            spot_lookup=spot_lookup,
            im_quote_lookup=quote_lookup,
        )
        spot = reference_price(
            "csi1000_spot",
            day,
            month,
            active_lookup=active_lookup,
            spot_lookup=spot_lookup,
            im_quote_lookup=quote_lookup,
        )
        matched = reference_price(
            "matched_expiry_im",
            day,
            month,
            active_lookup=active_lookup,
            spot_lookup=spot_lookup,
            im_quote_lookup=quote_lookup,
        )
        rows.append(
            {
                "date": day,
                "target_month": month,
                "active_im_close": active,
                "csi1000_spot_close": spot,
                "matched_expiry_im_close": matched,
                "active_vs_spot": np.nan if active is None or spot is None else active / spot,
                "matched_vs_spot": np.nan if matched is None or spot is None else matched / spot,
                "active95_as_spot_moneyness": np.nan
                if active is None or spot is None
                else 0.95 * active / spot,
            }
        )
    return pd.DataFrame(rows)


def contract_comparison(selection: pd.DataFrame) -> pd.DataFrame:
    subset = selection[
        selection["candidate"].isin(["active_im_m095", "csi1000_spot_m095"])
    ].copy()
    pieces = []
    for label, suffix in [
        ("active_im_m095", "active"),
        ("csi1000_spot_m095", "spot"),
    ]:
        frame = subset[subset["candidate"].eq(label)][
            [
                "signal_eval_date",
                "selected_contract",
                "selected_strike",
                "actual_execution_date",
                "actual_moneyness",
            ]
        ].rename(columns={column: f"{column}_{suffix}" for column in ["selected_contract", "selected_strike", "actual_execution_date", "actual_moneyness"]})
        pieces.append(frame)
    joined = pieces[0].merge(pieces[1], on="signal_eval_date", how="outer", validate="one_to_one")
    joined["same_contract"] = joined["selected_contract_active"].fillna("").eq(
        joined["selected_contract_spot"].fillna("")
    )
    joined["strike_difference_spot_minus_active"] = (
        joined["selected_strike_spot"] - joined["selected_strike_active"]
    )
    return joined


def decision_from_results(window: pd.DataFrame) -> tuple[str, str, dict[str, Any]]:
    table = window.set_index("candidate")

    def row(anchor: str, m: int) -> pd.Series:
        return table.loc[f"{PRIMARY_SCOPE}_{anchor}_m{m:03d}"]

    active95, spot95 = row("active_im", 95), row("csi1000_spot", 95)
    full_dd_better = float(spot95["max_dd_full"]) >= float(active95["max_dd_full"])
    full_return_ok = float(spot95["ann_return_full"]) >= float(active95["ann_return_full"]) - 0.005
    recent_dd_ok = all(
        float(spot95[f"max_dd_{window_name}"])
        >= float(active95[f"max_dd_{window_name}"]) - 0.01
        for window_name in ("last_3y", "last_1y")
    )
    neighbor_support: list[int] = []
    for m in (90, 100):
        active, spot = row("active_im", m), row("csi1000_spot", m)
        if (
            float(spot["max_dd_full"]) >= float(active["max_dd_full"])
            and float(spot["ann_return_full"]) >= float(active["ann_return_full"]) - 0.005
        ):
            neighbor_support.append(m)
    main_pass = bool(full_dd_better and full_return_ok and recent_dd_ok)
    if main_pass and neighbor_support:
        decision = "promote_candidate"
        stability = "wide_stable" if len(neighbor_support) == 2 else "narrow_stable"
    elif main_pass:
        decision = "watchlist"
        stability = "peak_only"
    else:
        decision = "keep_default"
        stability = "reject"
    detail = {
        "main_full_dd_better_or_equal": full_dd_better,
        "main_full_return_within_50bp": full_return_ok,
        "main_recent_dd_within_100bp": recent_dd_ok,
        "neighbor_support_moneyness": neighbor_support,
        "active95_full_ann_return": float(active95["ann_return_full"]),
        "spot95_full_ann_return": float(spot95["ann_return_full"]),
        "active95_full_max_dd": float(active95["max_dd_full"]),
        "spot95_full_max_dd": float(spot95["max_dd_full"]),
    }
    return decision, stability, detail


def write_outputs(
    daily: pd.DataFrame,
    trades: pd.DataFrame,
    lives: pd.DataFrame,
    scan_summary: pd.DataFrame,
    window_metrics: pd.DataFrame,
    selection: pd.DataFrame,
    selection_summary: dict[str, Any],
    basis: pd.DataFrame,
    comparison: pd.DataFrame,
    parity: dict[str, float],
    decision: str,
    stability: str,
    decision_detail: dict[str, Any],
    source_hashes: dict[str, str],
    inputs: dict[str, Any],
    git_before: str,
) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=False)
    daily.to_csv(OUTPUT / "daily_candidates.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "trade_audit.csv.gz", index=False, compression="gzip")
    lives.to_csv(OUTPUT / "lifecycle_audit.csv", index=False)
    scan_summary.to_csv(OUTPUT / "scan_summary.csv", index=False)
    window_metrics.to_csv(OUTPUT / "window_metrics.csv", index=False)
    selection.to_csv(OUTPUT / "contract_selection_audit.csv", index=False)
    basis.to_csv(OUTPUT / "basis_diagnostics.csv", index=False)
    comparison.to_csv(OUTPUT / "active_vs_spot_contract_comparison.csv", index=False)
    pd.DataFrame([parity]).to_csv(OUTPUT / "parity_checks.csv", index=False)
    worst = daily.sort_values("strategy_ret").groupby(["scope", "candidate"]).head(5)
    worst.to_csv(OUTPUT / "worst_days.csv", index=False)

    primary = window_metrics[
        window_metrics["candidate"].isin(
            [
                f"{PRIMARY_SCOPE}_active_im_m095",
                f"{PRIMARY_SCOPE}_csi1000_spot_m095",
                f"{PRIMARY_SCOPE}_matched_expiry_im_m095",
                f"{PRIMARY_SCOPE}_no_put",
            ]
        )
    ].copy()
    lines = [
        "# IM / MO Put 95% 行权价基准复测 v1",
        "",
        f"状态：`research_only`；决策：`{decision}`；稳定性：`{stability}`；未修改冻结主线。",
        "",
        "## 结论",
        "",
        f"- 正式真实样本：{daily['date'].min().date()}至{daily['date'].max().date()}，共{daily['date'].nunique()}个共同交易日。",
        "- 活动IM是冻结逐月路径当日实际持有的最近月合约，持有至最后交易日后切换；不是目标MO同到期远月IM。",
        f"- 主决策详情：`{json.dumps(decision_detail, ensure_ascii=False)}`。",
        f"- 选约最近行权价匹配率：{selection_summary['nearest_contract_match_rate']:.2%}；最大目标误差{selection_summary['max_target_error']:.6f}。",
        "",
        "## 95%主比较（IM 1.2核心Put、Call与网格关闭）",
        "",
        "| 候选 | 全期年化 | 全期MaxDD | 近3年年化 | 近3年MaxDD | 近1年年化 | 近1年MaxDD |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in primary.sort_values("candidate").itertuples(index=False):
        lines.append(
            f"| {row.base_candidate} | {row.ann_return_full:.2%} | {row.max_dd_full:.2%} | "
            f"{row.ann_return_last_3y:.2%} | {row.max_dd_last_3y:.2%} | "
            f"{row.ann_return_last_1y:.2%} | {row.max_dd_last_1y:.2%} |"
        )
    differing = int((~comparison["same_contract"]).sum())
    lines.extend(
        [
            "",
            "## 基差与选约",
            "",
            f"- 95%活动IM与现货候选在{differing}/{len(comparison)}个可比开仓或换月事件选中不同合约。",
            f"- 活动IM/现货在95%基准事件上的均值、中位数、最小值、最大值分别为"
            f"{basis['active_vs_spot'].mean():.4f}/{basis['active_vs_spot'].median():.4f}/"
            f"{basis['active_vs_spot'].min():.4f}/{basis['active_vs_spot'].max():.4f}。",
            "- 10年和5年窗口因真实IM/MO历史不足而截短为完整真实样本，不是独立长窗证据。",
            "",
            "## 验证与边界",
            "",
            f"- active95未修改执行引擎逐日复现误差：{parity['active_engine_parity_max_abs']:.3e}。",
            f"- 完整IM无Put冻结路径逐日误差：{parity['core_no_put_parity_max_abs']:.3e}。",
            f"- 50:50无Put真实路径逐日误差：{parity['im12_no_put_parity_max_abs']:.3e}。",
            "- 真实期货/期权使用官方原始日线；中证1000为价格指数、不复权；Asia/Shanghai共同交易日。",
            "- 未计盘口价差、冲击、容量、动态保证金、价格限制、异常结算和整数合约映射。",
            "- 本版只隔离Put行权价基准，Call与网格关闭，因此不是完整IM v1.2组合绩效。",
            "",
            "## 复现",
            "",
            "```powershell",
            "python -m pytest test_im_mo_put_strike_anchor_scan_v1.py -q",
            "python im_mo_put_strike_anchor_scan_v1.py",
            "```",
        ]
    )
    record = "\n".join(lines) + "\n"
    (OUTPUT / "record.md").write_text(record, encoding="utf-8")

    git_after = git_value("status", "--short")
    manifest = {
        "version": VERSION,
        "status": "research_only_not_live_authority",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "spec_sha256": SPEC_SHA256,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": source_hashes,
        "data_snapshot": {
            "start": daily["date"].min().date().isoformat(),
            "end": daily["date"].max().date().isoformat(),
            "rows": int(daily["date"].nunique()),
            "timezone": "Asia/Shanghai",
            "adjustment_mode": "official raw IM/MO daily bars; CSI1000 price index unadjusted",
        },
        "parent_v1_1_audit": inputs["parent_audit"],
        "parity": parity,
        "selection": selection_summary,
        "decision": decision,
        "stability_label": stability,
        "decision_detail": decision_detail,
        "git_status_before": git_before,
        "git_status_after": git_after,
        "warnings": [
            "real IM/MO history is shorter than five years",
            "official close is not guaranteed executable size",
            "Call and grid are disabled to isolate strike anchor",
            "research output is not a trading instruction",
        ],
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    command_text = (
        "python -m pytest test_im_mo_put_strike_anchor_scan_v1.py -q\n"
        "python im_mo_put_strike_anchor_scan_v1.py\n"
    )
    (OUTPUT / "command_log.txt").write_text(command_text, encoding="utf-8")

    scan_summary.to_csv(SCAN / "scan_summary.csv", index=False)
    window_metrics.to_csv(SCAN / "window_metrics.csv", index=False)
    (SCAN / "record.md").write_text(record, encoding="utf-8")
    with (SCAN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(command_text)
    meta_path = SCAN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "scan_type": "two_parameter_grid",
            "baseline": {
                "candidate": f"{PRIMARY_SCOPE}_active_im_m095",
                "anchor": "active_im",
                "moneyness": 0.95,
            },
            "candidate_grid": [
                {"anchor": anchor, "moneyness": moneyness}
                for anchor in ANCHORS
                for moneyness in MONEYNESS
            ],
            "data_snapshot": manifest["data_snapshot"],
            "cost_model": {
                "im_side_cost": 0.0001,
                "mo_contract_side_cost_full_im_notional": v4.MO_CONTRACT_SIDE_COST,
                "margin_buffer_per_1x_im": 0.30,
                "cash_yield_net_annual": 0.03,
            },
            "parity_check": parity,
            "warnings": manifest["warnings"],
            "source_hashes": source_hashes,
            "decision": decision,
            "stability_label": stability,
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    source_hashes = verify_preregistered_inputs()
    git_before = git_value("status", "--short")
    inputs = load_research_inputs()
    official_overlay, _ = official_active_reference(inputs)

    overlays: dict[str, pd.DataFrame] = {}
    trade_parts: list[pd.DataFrame] = []
    life_parts: list[pd.DataFrame] = []
    for anchor in ANCHORS:
        for moneyness in MONEYNESS:
            overlay, trades, lives = run_anchor_candidate(anchor, moneyness, inputs)
            label = candidate_name(anchor, moneyness)
            overlays[label] = overlay[
                [
                    "date",
                    "put_pnl_ret",
                    "put_cost_rate",
                    "put_mark_fraction",
                    "put_fraction",
                    "put_contract",
                ]
            ]
            if not trades.empty:
                trade_parts.append(trades)
            if not lives.empty:
                lives = lives.copy()
                lives["candidate"] = label
                life_parts.append(lives)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lives = pd.concat(life_parts, ignore_index=True, sort=False)

    parity_active = active_engine_parity(official_overlay, overlays["active_im_m095"])
    if parity_active > 1e-14:
        raise RuntimeError(f"Active-IM engine parity failed: {parity_active}")
    daily, parity = build_paths(inputs, overlays)
    parity["active_engine_parity_max_abs"] = parity_active
    scan_summary, window_metrics = metric_tables(daily)
    selection, selection_summary = selection_audit(trades, inputs)
    basis = basis_diagnostics(trades, inputs)
    comparison = contract_comparison(selection)
    decision, stability, decision_detail = decision_from_results(window_metrics)
    write_outputs(
        daily,
        trades,
        lives,
        scan_summary,
        window_metrics,
        selection,
        selection_summary,
        basis,
        comparison,
        parity,
        decision,
        stability,
        decision_detail,
        source_hashes,
        inputs,
        git_before,
    )
    print(
        json.dumps(
            {
                "version": VERSION,
                "decision": decision,
                "stability_label": stability,
                "data_start": daily["date"].min().date().isoformat(),
                "data_end": daily["date"].max().date().isoformat(),
                "rows": int(daily["date"].nunique()),
                "parity": parity,
                "selection": selection_summary,
                "output": str(OUTPUT),
                "scan": str(SCAN),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
