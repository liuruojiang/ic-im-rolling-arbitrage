from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import im_fixed_valuation_overlay_160_200_put_sync_v22 as v22
import im_mo_adaptive_valuation_tier_put_v10 as valuation_v10
import im_roll50_momentum50_fullcycle_put_v1 as put_model


ROOT = Path(__file__).resolve().parent
VERSION = "im_grid160_put_carry_objective_scan_v23"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_FILE = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
SPEC_SHA256 = "dd1a1b024d6f26c5528c631fbcd5eea3b1a89e853844fa27af235d0108ea99a1"
RUN = ROOT / "quant_param_scan_runs" / "20260823_im_grid160_put_carry_scan_v23"
DAILY_DIR = RUN / "daily_outputs"
PROXY_DAILY = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_proxy_v1" / "daily_nav.csv.gz"
V22_DAILY = ROOT / "outputs" / "im_fixed_valuation_overlay_160_200_put_sync_v22" / "daily_candidates.csv.gz"
V22_MANIFEST = ROOT / "outputs" / "im_fixed_valuation_overlay_160_200_put_sync_v22" / "output_manifest.json"

SCENARIOS = ("model_no_basis", "model_avg_basis", "real_actual_basis")
WINDOWS = ("full", "last_10y", "last_5y", "last_3y", "last_1y")
WINDOW_YEARS = {"full": None, "last_10y": 10, "last_5y": 5, "last_3y": 3, "last_1y": 1}
CANDIDATES: tuple[dict[str, Any], ...] = (
    {"candidate": "no_put", "valuation_family": "none", "mom_floor": 0},
    {"candidate": "legacy_3tier_mom3", "valuation_family": "legacy_3tier", "mom_floor": 3},
    {"candidate": "legacy_3tier_mom4", "valuation_family": "legacy_3tier", "mom_floor": 4},
    *tuple(
        {
            "candidate": f"current_4tier_mom{floor}",
            "valuation_family": "current_4tier",
            "mom_floor": floor,
        }
        for floor in range(5)
    ),
)
CURRENT = "current_4tier_mom4"
NO_PUT = "no_put"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_status() -> str:
    result = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def verify_inputs() -> dict[str, str]:
    if sha256(SPEC) != SPEC_SHA256:
        raise RuntimeError("Preregistered specification hash mismatch")
    if SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Preregistered specification sidecar mismatch")
    if not RUN.exists():
        raise FileNotFoundError(RUN)
    for output in (RUN / "scan_summary.csv", RUN / "window_metrics.csv", DAILY_DIR):
        if output.exists():
            raise FileExistsError(output)
    manifest = json.loads(V22_MANIFEST.read_text(encoding="utf-8"))
    mismatches = []
    for name, expected in manifest.items():
        path = V22_MANIFEST.parent / name
        actual = sha256(path) if path.exists() else "missing"
        if actual != expected:
            mismatches.append((name, actual, expected))
    if mismatches:
        raise RuntimeError(f"V22 frozen output mismatch: {mismatches}")
    paths = [
        SPEC,
        V22_MANIFEST,
        V22_DAILY,
        v22.V20_DAILY,
        v22.V19_DAILY,
        v22.BASIS_MANIFEST,
        PROXY_DAILY,
    ]
    return {str(path.relative_to(ROOT)): sha256(path) for path in paths}


def candidate_definition(name: str) -> dict[str, Any]:
    return next(item for item in CANDIDATES if item["candidate"] == name)


def build_states() -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    current, audit = put_model.current_rule_state()
    legacy = valuation_v10.load_v7_states()
    legacy = legacy[legacy["candidate"].eq("dual_w57_q750_850_950")][
        ["date", "final_tier"]
    ].rename(columns={"final_tier": "legacy_valuation_tier"})
    base = current.merge(legacy, on="date", how="left", validate="one_to_one")
    base["legacy_valuation_tier"] = base["legacy_valuation_tier"].fillna(
        base["absolute_tier"]
    ).astype(int)
    negative = base["momentum_120"].notna() & base["momentum_120"].lt(0.0)
    states: dict[str, pd.DataFrame] = {}
    for definition in CANDIDATES:
        state = base.copy()
        family = definition["valuation_family"]
        if family == "none":
            valuation_target = np.zeros(len(state), dtype=int)
            target = valuation_target.copy()
            momentum_target = np.zeros(len(state), dtype=int)
        else:
            valuation_target = state[
                "legacy_valuation_tier" if family == "legacy_3tier" else "valuation_tier"
            ].astype(int).to_numpy()
            floor = int(definition["mom_floor"])
            momentum_target = np.where(negative.to_numpy(), floor, 0).astype(int)
            target = np.maximum(valuation_target, momentum_target)
        state["valuation_tier"] = valuation_target
        state["mom120_active"] = negative
        state["mom120_floor_qty"] = momentum_target
        state["target_qty"] = target.astype(int)
        if not set(state["target_qty"].unique()).issubset({0, 1, 2, 3, 4}):
            raise RuntimeError(f"Invalid target quantities: {definition['candidate']}")
        states[definition["candidate"]] = state
    return states, audit


def zero_put(dates: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "put_pnl_ret": 0.0,
            "put_cost_rate": 0.0,
            "put_mark_fraction": 0.0,
            "put_fraction": 0.0,
            "put_contract": "",
        }
    )


def add_attribution(daily: pd.DataFrame) -> pd.DataFrame:
    proxy = pd.read_csv(
        PROXY_DAILY, parse_dates=["date"], usecols=["date", "csi1000_price_ret"]
    )
    result = daily.merge(proxy, on="date", how="left", validate="many_to_one")
    if result["csi1000_price_ret"].isna().any():
        raise RuntimeError("Missing CSI1000 return for carry attribution")
    real = result["scenario"].eq("real_actual_basis")
    factor = (1.0 + result["base_gross_ret"]) / (1.0 + result["csi1000_price_ret"]) - 1.0
    result["basis_carry_attribution"] = result["basis_carry_ret"]
    result.loc[real, "basis_carry_attribution"] = factor[real] * (
        1.0 + result.loc[real, "overlay_held_before"].astype(float)
    )
    result["put_qty"] = 2.0 * result["put_fraction"]
    return result


def build_metrics(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    no_put = daily[daily["variant"].eq(NO_PUT)][["scenario", "date", "cash_ret"]].rename(
        columns={"cash_ret": "no_put_cash_ret"}
    )
    frame = daily.merge(no_put, on=["scenario", "date"], validate="many_to_one")
    frame["put_total_effect_ret"] = frame["cash_ret"] - frame["no_put_cash_ret"]
    rows: list[dict[str, Any]] = []
    for label, group in frame.groupby("candidate", sort=False):
        group = group.sort_values("date")
        first = group.iloc[0]
        end = pd.Timestamp(group["date"].max())
        definition = candidate_definition(str(first["variant"]))
        for segment in WINDOWS:
            years = WINDOW_YEARS[segment]
            requested = group["date"].min() if years is None else end - pd.DateOffset(years=years)
            start = max(pd.Timestamp(group["date"].min()), pd.Timestamp(requested))
            sample = group[group["date"].ge(start)]
            metric = v22.grid.metrics(sample["cash_ret"])
            basis_sum = float(sample["basis_carry_attribution"].sum())
            put_effect = float(sample["put_total_effect_ret"].sum())
            rows.append(
                {
                    "candidate": label,
                    "scenario": first["scenario"],
                    "variant": first["variant"],
                    "valuation_family": definition["valuation_family"],
                    "mom_floor": definition["mom_floor"],
                    "segment": segment,
                    "start": sample["date"].min(),
                    "end": end,
                    "rows": len(sample),
                    "coverage_complete": bool(years is None or group["date"].min() <= requested + pd.Timedelta(days=7)),
                    "ann_return": metric["ann_return"],
                    "ann_vol": metric["ann_vol"],
                    "sharpe_repo": metric["sharpe_repo"],
                    "max_dd": metric["max_dd"],
                    "basis_carry_sum": basis_sum,
                    "put_total_effect_sum": put_effect,
                    "put_effect_to_basis": put_effect / basis_sum if abs(basis_sum) > 1e-12 else np.nan,
                    "put_cost_total": float(sample["put_cost_rate"].sum()),
                    "avg_put_qty": float(sample["put_qty"].mean()),
                    "max_put_qty": float(sample["put_qty"].max()),
                    "target4_held_days": int(sample["put_qty"].ge(4.0 - 1e-12).sum()),
                    "down_market_put_effect_sum": float(
                        sample.loc[sample["csi1000_price_ret"].lt(0.0), "put_total_effect_ret"].sum()
                    ),
                    "rebound_gt1_put_effect_sum": float(
                        sample.loc[sample["csi1000_price_ret"].gt(0.01), "put_total_effect_ret"].sum()
                    ),
                }
            )
    long = pd.DataFrame(rows)
    baseline = long[long["variant"].eq(NO_PUT)].set_index(["scenario", "segment"])
    current = long[long["variant"].eq(CURRENT)].set_index(["scenario", "segment"])
    long["ann_return_delta_vs_no_put"] = [
        row.ann_return - float(baseline.loc[(row.scenario, row.segment), "ann_return"])
        for row in long.itertuples(index=False)
    ]
    long["max_dd_delta_vs_no_put"] = [
        row.max_dd - float(baseline.loc[(row.scenario, row.segment), "max_dd"])
        for row in long.itertuples(index=False)
    ]
    long["ann_return_delta_vs_current"] = [
        row.ann_return - float(current.loc[(row.scenario, row.segment), "ann_return"])
        for row in long.itertuples(index=False)
    ]
    long["max_dd_delta_vs_current"] = [
        row.max_dd - float(current.loc[(row.scenario, row.segment), "max_dd"])
        for row in long.itertuples(index=False)
    ]
    wide_rows: list[dict[str, Any]] = []
    for label, group in long.groupby("candidate", sort=False):
        first = group.iloc[0]
        row: dict[str, Any] = {
            "candidate": label,
            "scenario": first["scenario"],
            "variant": first["variant"],
            "valuation_family": first["valuation_family"],
            "mom_floor": first["mom_floor"],
        }
        full = group[group["segment"].eq("full")].iloc[0]
        for field in (
            "basis_carry_sum",
            "put_total_effect_sum",
            "put_effect_to_basis",
            "put_cost_total",
            "avg_put_qty",
            "max_put_qty",
            "target4_held_days",
            "down_market_put_effect_sum",
            "rebound_gt1_put_effect_sum",
        ):
            row[f"{field}_full"] = full[field]
        for item in group.itertuples(index=False):
            for metric in (
                "ann_return",
                "ann_vol",
                "sharpe_repo",
                "max_dd",
                "ann_return_delta_vs_no_put",
                "max_dd_delta_vs_no_put",
                "ann_return_delta_vs_current",
                "max_dd_delta_vs_current",
            ):
                row[f"{metric}_{item.segment}"] = getattr(item, metric)
        wide_rows.append(row)
    return long, pd.DataFrame(wide_rows)


def capital_audit(daily: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    trade_dates = set(
        (str(row.scenario), str(row.variant), pd.Timestamp(row.actual_execution_date))
        for row in trades.itertuples(index=False)
    )
    rows = []
    for label, group in daily.groupby("candidate", sort=False):
        first = group.iloc[0]
        cash15 = 1.0 - 0.15 * group["total_im_units"] - group["put_mark_fraction"] - group["call_margin_fraction"]
        put_day = group["date"].map(
            lambda day: (str(first["scenario"]), str(first["variant"]), pd.Timestamp(day)) in trade_dates
        )
        rows.append(
            {
                "candidate": label,
                "scenario": first["scenario"],
                "variant": first["variant"],
                "cash30_breach_rows": int(group["cash_weight_raw"].lt(-1e-12).sum()),
                "min_cash30_raw": float(group["cash_weight_raw"].min()),
                "cash15_breach_rows": int(cash15.lt(-1e-12).sum()),
                "min_cash15_raw": float(cash15.min()),
                "put_execution_cash15_breach_rows": int((put_day & cash15.lt(-1e-12)).sum()),
                "max_put_mark_fraction": float(group["put_mark_fraction"].max()),
                "max_put_qty": float(group["put_qty"].max()),
            }
        )
    return pd.DataFrame(rows)


def cycle_metrics(daily: pd.DataFrame, model_grid: pd.DataFrame, real_grid: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario, source in (
        ("model_no_basis", model_grid),
        ("model_avg_basis", model_grid),
        ("real_actual_basis", real_grid),
    ):
        buys = source.loc[source["overlay_buy"].eq(1), "date"].sort_values().tolist()
        sells = source.loc[source["overlay_sell"].eq(1), "date"].sort_values().tolist()
        for variant in [item["candidate"] for item in CANDIDATES]:
            group = daily[
                daily["scenario"].eq(scenario) & daily["variant"].eq(variant)
            ].sort_values("date")
            for cycle_id, (start, end) in enumerate(zip(buys, sells), start=1):
                sample = group[group["date"].between(start, end)]
                nav = (1.0 + sample["cash_ret"]).cumprod()
                rows.append(
                    {
                        "scenario": scenario,
                        "variant": variant,
                        "cycle_id": cycle_id,
                        "start": start,
                        "end": end,
                        "rows": len(sample),
                        "max_dd": float((nav / nav.cummax() - 1.0).min()),
                        "put_effect_sum": float(
                            sample["cash_ret"].sum()
                            - daily[
                                daily["scenario"].eq(scenario)
                                & daily["variant"].eq(NO_PUT)
                                & daily["date"].between(start, end)
                            ]["cash_ret"].sum()
                        ),
                    }
                )
    return pd.DataFrame(rows)


def decide(wide: pd.DataFrame, capital: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    table = wide.merge(capital, on=["candidate", "scenario", "variant"], validate="one_to_one")
    index = table.set_index(["scenario", "variant"])
    rows = []
    for definition in CANDIDATES:
        variant = definition["candidate"]
        if variant == NO_PUT:
            continue
        real = index.loc[("real_actual_basis", variant)]
        model = index.loc[("model_avg_basis", variant)]
        no_real = index.loc[("real_actual_basis", NO_PUT)]
        no_model = index.loc[("model_avg_basis", NO_PUT)]
        real_dd = max(
            float(real["max_dd_full"] - no_real["max_dd_full"]),
            float(real["max_dd_last_3y"] - no_real["max_dd_last_3y"]),
        )
        model_dd = max(
            float(model["max_dd_full"] - no_model["max_dd_full"]),
            float(model["max_dd_last_5y"] - no_model["max_dd_last_5y"]),
        )
        base_pass = bool(
            real_dd >= 0.03 - 1e-12
            and model_dd >= 0.03 - 1e-12
            and float(real["ann_return_full"] - no_real["ann_return_full"]) >= -0.02 - 1e-12
            and float(model["ann_return_full"] - no_model["ann_return_full"]) >= -0.02 - 1e-12
            and int(real["cash15_breach_rows"]) == 0
            and int(real["put_execution_cash15_breach_rows"]) == 0
        )
        rows.append(
            {
                "variant": variant,
                "real_dd_improvement_best": real_dd,
                "model_avg_dd_improvement_best": model_dd,
                "real_full_cagr_delta_vs_no_put": float(real["ann_return_full"] - no_real["ann_return_full"]),
                "model_avg_full_cagr_delta_vs_no_put": float(model["ann_return_full"] - no_model["ann_return_full"]),
                "real_full_sharpe": float(real["sharpe_repo_full"]),
                "real_3y_sharpe": float(real["sharpe_repo_last_3y"]),
                "real_avg_put_qty": float(real["avg_put_qty_full"]),
                "real_put_cost_total": float(real["put_cost_total_full"]),
                "base_gate_pass": base_pass,
            }
        )
    decisions = pd.DataFrame(rows)
    decisions["neighbor_support"] = False
    for idx, row in decisions.iterrows():
        definition = candidate_definition(str(row["variant"]))
        if definition["valuation_family"] == "current_4tier":
            floor = int(definition["mom_floor"])
            neighbors = {f"current_4tier_mom{floor - 1}", f"current_4tier_mom{floor + 1}"}
        else:
            neighbors = {"legacy_3tier_mom3", "legacy_3tier_mom4", "current_4tier_mom3", "current_4tier_mom4"}
            neighbors.discard(str(row["variant"]))
        decisions.loc[idx, "neighbor_support"] = bool(
            decisions[decisions["variant"].isin(neighbors)]["base_gate_pass"].any()
        )
    decisions["carry_compatible_pass"] = decisions["base_gate_pass"] & decisions["neighbor_support"]
    current_real = index.loc[("real_actual_basis", CURRENT)]
    decisions["lighter_than_current_pass"] = False
    for idx, row in decisions.iterrows():
        variant = str(row["variant"])
        real = index.loc[("real_actual_basis", variant)]
        decisions.loc[idx, "lighter_than_current_pass"] = bool(
            variant != CURRENT
            and float(real["ann_return_full"] - current_real["ann_return_full"]) >= -0.01 - 1e-12
            and float(real["max_dd_full"] - current_real["max_dd_full"]) >= -0.01 - 1e-12
            and float(real["avg_put_qty_full"]) <= 0.85 * float(current_real["avg_put_qty_full"]) + 1e-12
        )
    eligible = decisions[decisions["carry_compatible_pass"]].copy()
    lighter = eligible[eligible["lighter_than_current_pass"]].copy()
    pool = lighter if len(lighter) else eligible
    if len(pool):
        chosen = str(
            pool.sort_values(
                ["real_full_sharpe", "real_3y_sharpe", "real_avg_put_qty", "real_put_cost_total"],
                ascending=[False, False, True, True],
            ).iloc[0]["variant"]
        )
        decision = "watchlist_lighter_put" if chosen != CURRENT else "keep_current_four_tier_mom4"
        stability = "narrow_stable" if len(eligible) >= 2 else "peak_only"
    else:
        chosen = CURRENT
        decision = "keep_current_four_tier_mom4"
        stability = "reject"
    decisions["selected"] = decisions["variant"].eq(chosen)
    return decisions, {"decision": decision, "stability_label": stability, "selected": chosen}


def main() -> None:
    started = datetime.now().astimezone()
    git_before = git_status()
    source_hashes = verify_inputs()
    basis = json.loads(v22.BASIS_MANIFEST.read_text(encoding="utf-8"))["proxy_assumption"]
    basis_daily = float(basis["daily_geometric"])
    states, state_audit = build_states()

    model_market, model_market_checks = v22.v6.model_market()
    upstream, _, _, _, _, raw_options = v22.v4.load_inputs()
    active_im = v22.v8.active_im_closes(upstream)
    expiry_map = v22.v4.actual_expiry_map(raw_options, upstream)
    options = v22.v4.prepare_options(raw_options, expiry_map)
    model_dates = pd.DatetimeIndex(model_market["date"])
    real_dates = pd.DatetimeIndex(upstream["date"])
    model_grid = v22.load_source(v22.V20_DAILY, v22.SOURCE_GRID)
    real_grid = v22.load_source(v22.V19_DAILY, v22.SOURCE_GRID)

    daily_parts: list[pd.DataFrame] = []
    schedule_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    lifecycle_parts: list[pd.DataFrame] = []
    generated_puts: dict[tuple[str, str], pd.DataFrame] = {}
    for definition in CANDIDATES:
        variant = definition["candidate"]
        model_schedule = put_model.im_v12.build_momentum_schedule(
            states[variant], variant, model_dates, f"v23_{variant}"
        )
        real_schedule = put_model.im_v12.build_momentum_schedule(
            states[variant], variant, real_dates, f"v23_{variant}"
        )
        if variant == NO_PUT:
            model_put = zero_put(model_market["date"])
            real_put = zero_put(upstream["date"])
            model_trades = real_trades = model_lives = real_lives = pd.DataFrame()
        else:
            model_put, model_trades, model_lives = v22.v8.run_model_normal_close(
                model_market, model_schedule, "3m", 0.95, variant
            )
            real_put, real_trades, real_lives = v22.v8.run_real_normal_close(
                upstream, options, active_im, real_schedule, "3m", 0.95, variant
            )
        generated_puts[("model", variant)] = model_put
        generated_puts[("real", variant)] = real_put
        schedule_parts.extend(
            [model_schedule.assign(layer="model"), real_schedule.assign(layer="real")]
        )
        if len(model_trades):
            trade_parts.extend(
                [
                    model_trades.assign(scenario="model_no_basis", variant=variant),
                    model_trades.assign(scenario="model_avg_basis", variant=variant),
                ]
            )
        if len(real_trades):
            trade_parts.append(real_trades.assign(scenario="real_actual_basis", variant=variant))
        if len(model_lives):
            lifecycle_parts.append(model_lives.assign(layer="model", variant=variant))
        if len(real_lives):
            lifecycle_parts.append(real_lives.assign(layer="real", variant=variant))
        daily_parts.extend(
            [
                v22.compose(model_grid, model_put, "model_no_basis", variant, 0.0),
                v22.compose(model_grid, model_put, "model_avg_basis", variant, basis_daily),
                v22.compose(real_grid, real_put, "real_actual_basis", variant, 0.0),
            ]
        )
    daily = add_attribution(
        pd.concat(daily_parts, ignore_index=True, sort=False).sort_values(
            ["scenario", "variant", "date"]
        )
    )
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    lifecycles = pd.concat(lifecycle_parts, ignore_index=True, sort=False)
    schedules = pd.concat(schedule_parts, ignore_index=True, sort=False)

    metrics, wide = build_metrics(daily)
    capital = capital_audit(daily, trades)
    cycles = cycle_metrics(daily, model_grid, real_grid)
    decisions, summary = decide(wide, capital)
    real_price_audit, real_price_stats = v22.v18.generic_price_integrity(
        trades[trades["scenario"].eq("real_actual_basis")].assign(layer="real"),
        raw_options,
    )

    frozen = pd.read_csv(V22_DAILY, parse_dates=["date"], low_memory=False)
    parity_columns = ["put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"]
    parity_rows = []
    for layer, scenario in (("model", "model_no_basis"), ("real", "real_actual_basis")):
        reference = frozen[
            frozen["scenario"].eq(scenario) & frozen["variant"].eq("grid_core_put")
        ]
        generated = generated_puts[(layer, CURRENT)]
        joined = generated[["date", *parity_columns]].merge(
            reference[["date", *parity_columns]],
            on="date",
            suffixes=("_generated", "_frozen"),
            validate="one_to_one",
        )
        for column in parity_columns:
            parity_rows.append(
                {
                    "layer": layer,
                    "field": column,
                    "max_abs": float(
                        (joined[f"{column}_generated"] - joined[f"{column}_frozen"]).abs().max()
                    ),
                }
            )
    parity = pd.DataFrame(parity_rows)
    max_parity = float(parity["max_abs"].max())
    expected_ret = (
        (1.0 + daily["gross_before_cost"])
        * (1.0 - daily["futures_cost_rate"])
        * (1.0 - daily["put_cost_rate"])
        * (1.0 - daily["call_cost_rate"])
        - 1.0
    )
    expected_cash = daily["ret"] + daily["cash_weight"] * v22.grid.CASH_DAILY
    nav = daily.groupby("candidate", sort=False)["cash_ret"].transform(
        lambda values: (1.0 + values).cumprod()
    )
    checks = {
        "current_put_parity_max_abs": max_parity,
        "return_identity_max_abs": float((daily["ret"] - expected_ret).abs().max()),
        "cash_identity_max_abs": float((daily["cash_ret"] - expected_cash).abs().max()),
        "nav_identity_max_abs": float((daily["nav"] - nav).abs().max()),
        "candidate_count": int(daily["candidate"].nunique()),
        "duplicate_candidate_dates": int(daily.duplicated(["candidate", "date"]).sum()),
        "model_rows": sorted(
            daily[daily["scenario"].str.startswith("model")].groupby("candidate").size().unique().tolist()
        ),
        "real_rows": sorted(
            daily[daily["scenario"].eq("real_actual_basis")].groupby("candidate").size().unique().tolist()
        ),
        "model_market": model_market_checks,
        "real_price_integrity": real_price_stats,
    }
    checks["all_checks_passed"] = bool(
        max_parity <= 1e-12
        and checks["return_identity_max_abs"] <= 1e-12
        and checks["cash_identity_max_abs"] <= 1e-12
        and checks["nav_identity_max_abs"] <= 1e-12
        and checks["candidate_count"] == 24
        and checks["duplicate_candidate_dates"] == 0
        and checks["model_rows"] == [2756]
        and checks["real_rows"] == [986]
        and real_price_stats["trade_legs"] > 0
        and real_price_stats["max_close_price_error"] <= 1e-14
        and all(
            real_price_stats[key] == 0
            for key in (
                "nonpositive_close_rows",
                "nonpositive_volume_rows",
                "new_leg_nonpositive_oi_rows",
            )
        )
    )
    if not checks["all_checks_passed"]:
        raise RuntimeError(f"Integrity checks failed: {checks}")

    DAILY_DIR.mkdir(parents=True, exist_ok=False)
    daily.to_csv(DAILY_DIR / "daily_candidates.csv.gz", index=False, compression="gzip")
    schedules.to_csv(DAILY_DIR / "put_target_schedules.csv.gz", index=False, compression="gzip")
    trades.to_csv(DAILY_DIR / "put_trades.csv.gz", index=False, compression="gzip")
    lifecycles.to_csv(DAILY_DIR / "put_lifecycles.csv.gz", index=False, compression="gzip")
    metrics.to_csv(RUN / "scan_summary.csv", index=False)
    wide.to_csv(RUN / "window_metrics.csv", index=False)
    capital.to_csv(RUN / "capital_audit.csv", index=False)
    cycles.to_csv(RUN / "cycle_metrics.csv", index=False)
    decisions.to_csv(RUN / "candidate_decisions.csv", index=False)
    parity.to_csv(RUN / "parity_checks.csv", index=False)
    real_price_audit.to_csv(RUN / "real_put_price_audit.csv", index=False)
    (RUN / "integrity_checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )

    focus = wide[
        wide["scenario"].eq("real_actual_basis")
        | wide["scenario"].eq("model_avg_basis")
    ][
        [
            "scenario",
            "variant",
            "ann_return_full",
            "sharpe_repo_full",
            "max_dd_full",
            "avg_put_qty_full",
            "put_cost_total_full",
            "put_total_effect_sum_full",
            "basis_carry_sum_full",
        ]
    ]
    record = f"""# IM 1.60/2.00 网格下 Put 与贴水收益目标复评 v23

## Run Metadata

- Run id: `20260823_im_grid160_put_carry_scan_v23`；Asia/Shanghai。
- Scan type: `candidate_bundle`；source-change rule: `research_only_no_source_change`。
- Entrypoint: `{Path(__file__).name}`。

## Research Question

- 固定IM网格1.60/2.00、Call不变、网格仓不加Put，比较无Put、旧三档与当前四档下不同MOM120最低张数。
- 目标是识别与滚IM贴水收益兼容的保护强度，而不是只选最高CAGR。

## Implementation Anchor

- 组合底座：v19/v20冻结的1.60/2.00网格路径；Put引擎：`im_mo_close_execution_v8`。
- 当前四档4张模型/真实Put组件复现误差：{max_parity:.3e}。
- 候选数：8种Put规则 × 3个情景 = 24条路径。

## Data Snapshot

- Real: 2022-07-22—2026-08-14，官方CFFEX IM/MO，实际基差已在期货收益中。
- Model: 2015-04-16—2026-08-14；理论MO；平均基差情景年化{float(basis['annual_geometric']):.2%}，含未来信息回填。
- 中证1000价格指数收益仅用于基差与涨跌状态归因，不重复加入真实收益。

## Cost and Execution Assumptions

- 网格T+1开盘；Put T+1官方收盘；3个月目标期限；95%行权价；月度重置。
- 每1倍IM 30%保证金/缓冲，剩余正现金年化3%；另审计用户15%操作口径。
- 未计点差、冲击、容量、涨跌停未成交、动态保证金上调与税费。

## Runtime Override Plan

- 所有候选通过独立日程传入官方模型/真实引擎；无冻结常量被修改。
- `current_4tier_mom4`与v22逐日Put组件校验通过。

## Commands

```powershell
python {Path(__file__).name}
python -m pytest -q test_im_grid160_put_carry_objective_scan_v23.py
```

## Output Files

- `scan_summary.csv`、`window_metrics.csv`、`candidate_decisions.csv`。
- `capital_audit.csv`、`cycle_metrics.csv`、`parity_checks.csv`、`daily_outputs/`。

## Full-Sample Results

```text
{focus.to_string(index=False)}
```

## Window Results

- 完整full/10Y/5Y/3Y/1Y见`scan_summary.csv`和`window_metrics.csv`；真实5Y/10Y覆盖不足已标记。

## Stability Classification

- Stability: `{summary['stability_label']}`。
- Selected: `{summary['selected']}`。

## Decision

- Decision: `{summary['decision']}`。
- 本轮只形成研究观察结论，不修改冻结V2或任何下单路径。

## User-Facing Summary

- Put是否违反贴水初衷，按实际基差贡献、Put总效果、回撤改善、反弹拖累、平均张数和资本占用共同判断。
"""
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"python {Path(__file__).name}\n")
        handle.write("python -m pytest -q test_im_grid160_put_carry_objective_scan_v23.py\n")

    meta_path = RUN / "scan_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "phase": "run_complete_pending_audit",
            "scan_type": "candidate_bundle",
            "baseline": {"variant": NO_PUT, "current": CURRENT, "grid": "1.60/2.00"},
            "candidate_grid": list(CANDIDATES),
            "data_snapshot": {
                "model": ["2015-04-16", "2026-08-14"],
                "real": ["2022-07-22", "2026-08-14"],
                "average_basis_daily": basis_daily,
                "average_basis_annual": float(basis["annual_geometric"]),
                "lookahead_warning": basis["lookahead_warning"],
                "state_audit": state_audit,
            },
            "cost_model": {
                "grid_one_way": v22.grid.ONE_WAY_COST,
                "grid_roll_round_trip": 2 * v22.grid.ONE_WAY_COST,
                "margin_buffer_per_im": 0.30,
                "operational_margin_user_assumption": 0.15,
                "cash_annual": 0.03,
                "put_execution": "T+1 official close",
            },
            "parity_check": {"current_put_max_abs": max_parity},
            "source_hashes": source_hashes,
            "decision": summary["decision"],
            "stability_label": summary["stability_label"],
            "selected_candidate": summary["selected"],
            "git_status_before": git_before,
            "git_status_after": git_status(),
            "elapsed_sec": (datetime.now().astimezone() - started).total_seconds(),
            "warnings": [
                "Real IM/MO history is under five years",
                "2015 model options are theoretical",
                "Average basis scenario backfills a post-listing mean and has look-ahead",
                "15 percent operational margin is user-provided and not broker-verified",
                "Research only; frozen V2 unchanged",
            ],
            "outputs": {
                "record": str((RUN / "record.md").resolve()),
                "scan_summary": str((RUN / "scan_summary.csv").resolve()),
                "window_metrics": str((RUN / "window_metrics.csv").resolve()),
                "scan_meta": str(meta_path.resolve()),
                "command_log": str((RUN / "command_log.txt").resolve()),
            },
        }
    )
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(focus.to_string(index=False))
    print(decisions.to_string(index=False))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
