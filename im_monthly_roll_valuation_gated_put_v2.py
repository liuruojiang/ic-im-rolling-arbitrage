from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ic_monthly_discount_roll_v1 import third_friday
from im_monthly_roll_valuation_gated_put_v1 import walk_forward_forecast


ROOT = Path(__file__).resolve().parent
VERSION = "im_monthly_roll_valuation_gated_put_v2"
OUTPUT_DIR = ROOT / "outputs" / VERSION
SPEC_PATH = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH_PATH = ROOT / "docs" / f"{VERSION}_spec.sha256"

PUT_V1_OUTPUT = ROOT / "outputs" / "im_monthly_roll_3m_lowest_put_v1"
PUT_V1_DATA = ROOT / "data" / "im_monthly_roll_3m_lowest_put_v1"
GATED_V1_OUTPUT = ROOT / "outputs" / "im_monthly_roll_valuation_gated_put_v1"
VALUATION_V3_OUTPUT = ROOT / "outputs" / "ic_im_valuation_risk_premium_forecast_v3"
VALUATION_V3_DATA = ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3"

START_DATE = pd.Timestamp("2022-07-22")
END_DATE = pd.Timestamp("2026-08-14")
FIRST_SIGNAL_STATE = pd.Timestamp("2022-06-30")
TRADING_DAYS = 252
PUT_ONE_WAY_COST = 0.0001
CASH_WEIGHT = 0.70
CASH_ANNUAL_RETURN = 0.03
CASH_DAILY_RETURN = (1.0 + CASH_ANNUAL_RETURN) ** (1.0 / TRADING_DAYS) - 1.0
MO_RE = re.compile(r"^MO(?P<yy>\d{2})(?P<mm>\d{2})-P-(?P<strike>\d+)$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_spec() -> str:
    if not SPEC_PATH.exists() or not SPEC_HASH_PATH.exists():
        raise FileNotFoundError("Frozen v2 specification or SHA-256 file is missing")
    expected = SPEC_HASH_PATH.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256_file(SPEC_PATH)
    if expected != actual:
        raise RuntimeError(f"Frozen spec hash mismatch: expected {expected}, actual {actual}")
    return actual


def add_option_expiry(options: pd.DataFrame) -> pd.DataFrame:
    result = options.copy()
    parsed = result["contract"].str.extract(MO_RE)
    result["contract_month"] = pd.to_datetime(
        "20" + parsed["yy"] + "-" + parsed["mm"] + "-01", errors="coerce"
    )
    result["strike"] = pd.to_numeric(parsed["strike"], errors="coerce")
    if result[["contract_month", "strike"]].isna().any().any():
        raise RuntimeError("Invalid MO put contract code in frozen option data")
    result["rule_expiry"] = result["contract_month"].map(third_friday)
    return result


def build_decision_schedule(
    states: pd.DataFrame,
    tri: pd.DataFrame,
    options: pd.DataFrame,
    daily: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    trade_dates = pd.DatetimeIndex(sorted(daily["date"].unique()))
    relevant_states = states[
        (states["date"] >= FIRST_SIGNAL_STATE) & (states["date"] < END_DATE)
    ].copy()
    decision_rows: list[dict[str, object]] = []
    analogue_parts: list[pd.DataFrame] = []
    for decision_number, state in enumerate(relevant_states.itertuples(index=False)):
        later_dates = trade_dates[trade_dates > state.date]
        if not len(later_dates):
            continue
        execution_date = pd.Timestamp(later_dates[0])
        if execution_date < START_DATE:
            execution_date = START_DATE
        if execution_date > END_DATE:
            continue

        decision_id = f"v2_{decision_number:02d}_{pd.Timestamp(state.date).date().isoformat()}"
        signal, analogues = walk_forward_forecast(states, tri, pd.Timestamp(state.date), decision_id)
        analogue_parts.append(analogues)

        chain = options[
            options["date"].eq(execution_date) & (options["rule_expiry"] > execution_date)
        ].copy()
        if chain.empty:
            raise RuntimeError(f"No listed MO put chain on {execution_date.date()}")
        target_date = pd.Timestamp(state.date) + pd.DateOffset(months=3)
        expiries = chain[["contract_month", "rule_expiry"]].drop_duplicates().copy()
        expiries["expiry_distance_days"] = (expiries["rule_expiry"] - target_date).abs().dt.days
        expiries["earlier_than_target"] = (expiries["rule_expiry"] < target_date).astype(int)
        selected_expiry = expiries.sort_values(
            ["expiry_distance_days", "earlier_than_target", "rule_expiry"]
        ).iloc[0]
        selected_chain = chain[chain["rule_expiry"].eq(selected_expiry["rule_expiry"])].sort_values(
            ["strike", "contract"]
        )
        selected = selected_chain.iloc[0]
        if not (
            float(selected["open"]) > 0
            and float(selected["settle"]) > 0
            and float(selected["volume"]) > 0
            and float(selected["open_interest"]) > 0
        ):
            raise RuntimeError(
                f"Selected literal-lowest put is not open-executable on {execution_date.date()}: "
                f"{selected['contract']}"
            )
        future_row = daily.loc[daily["date"].eq(execution_date)].iloc[0]
        decision_rows.append(
            {
                **signal,
                "state_date": pd.Timestamp(state.date),
                "execution_date": execution_date,
                "execution_lag_calendar_days": int((execution_date - pd.Timestamp(state.date)).days),
                "initial_listing_exception": bool(execution_date == START_DATE and pd.Timestamp(state.date) < START_DATE),
                "target_date": target_date,
                "selected_contract_month": pd.Timestamp(selected["contract_month"]),
                "selected_expiry": pd.Timestamp(selected["rule_expiry"]),
                "expiry_distance_days": int(selected_expiry["expiry_distance_days"]),
                "put_contract": selected["contract"],
                "strike": float(selected["strike"]),
                "entry_open": float(selected["open"]),
                "entry_settle": float(selected["settle"]),
                "entry_volume": float(selected["volume"]),
                "entry_open_interest": float(selected["open_interest"]),
                "im_contract_on_execution": future_row["contract"],
                "im_settle_on_execution": float(future_row["settle"]),
                "strike_vs_im": float(selected["strike"] / future_row["settle"] - 1.0),
                "open_premium_to_im_notional": float(selected["open"] / future_row["settle"]),
            }
        )

    decisions = pd.DataFrame(decision_rows).sort_values("execution_date").reset_index(drop=True)
    if decisions["execution_date"].duplicated().any():
        raise RuntimeError("Duplicate immediate execution dates")
    decisions["next_execution_date"] = decisions["execution_date"].shift(-1)
    decisions.loc[decisions.index[-1], "next_execution_date"] = END_DATE

    lookup = options.set_index(["contract", "date"])
    exit_open, exit_volume, exit_oi = [], [], []
    for idx, row in decisions.iterrows():
        exit_date = pd.Timestamp(row["next_execution_date"])
        if idx == len(decisions) - 1:
            exit_open.append(np.nan)
            exit_volume.append(np.nan)
            exit_oi.append(np.nan)
            continue
        key = (row["put_contract"], exit_date)
        if key not in lookup.index:
            raise RuntimeError(f"Old put missing on immediate roll date: {key}")
        exit_row = lookup.loc[key]
        if not (
            float(exit_row["open"]) > 0
            and float(exit_row["volume"]) > 0
            and float(exit_row["open_interest"]) > 0
        ):
            raise RuntimeError(f"Old put has non-executable open on {exit_date.date()}: {row['put_contract']}")
        exit_open.append(float(exit_row["open"]))
        exit_volume.append(float(exit_row["volume"]))
        exit_oi.append(float(exit_row["open_interest"]))
    decisions["next_decision_exit_open"] = exit_open
    decisions["next_decision_exit_volume"] = exit_volume
    decisions["next_decision_exit_open_interest"] = exit_oi

    analogues = pd.concat(analogue_parts, ignore_index=True)
    if not (decisions["execution_date"] > decisions["state_date"]).all():
        initial = decisions.iloc[0]
        if not (
            initial["initial_listing_exception"]
            and initial["execution_date"] == START_DATE
            and (decisions.iloc[1:]["execution_date"] > decisions.iloc[1:]["state_date"]).all()
        ):
            raise RuntimeError("Non-causal valuation execution date")
    if (analogues["forward_end_date"] > analogues["as_of"]).any():
        raise RuntimeError("Walk-forward analogue outcome leakage")
    return decisions, analogues


def build_option_overlay(
    base_daily: pd.DataFrame,
    decisions: pd.DataFrame,
    options: pd.DataFrame,
    signal_column: str,
    label: str,
) -> pd.DataFrame:
    daily = base_daily[["date", "contract", "settle"]].copy().sort_values("date").reset_index(drop=True)
    daily[f"{label}_put_pnl_ret"] = 0.0
    daily[f"{label}_put_cost_rate"] = 0.0
    daily[f"{label}_put_contract"] = ""
    daily[f"{label}_put_settle"] = np.nan
    daily[f"{label}_signal_on"] = False
    option_lookup = options.set_index(["contract", "date"])
    event_lookup = {pd.Timestamp(row.execution_date): row for row in decisions.itertuples(index=False)}

    active_contract: str | None = None
    prior_option_settle: float | None = None
    for day_index, day_row in daily.iterrows():
        day = pd.Timestamp(day_row["date"])
        denominator = float(daily.loc[day_index - 1, "settle"]) if day_index > 0 else float(day_row["settle"])
        points_pnl = 0.0
        if day in event_lookup:
            decision = event_lookup[day]
            desired_on = bool(getattr(decision, signal_column))
            is_initial_exception = bool(decision.initial_listing_exception)
            side_count = 0
            if active_contract is not None:
                old_row = option_lookup.loc[(active_contract, day)]
                if prior_option_settle is None:
                    raise RuntimeError("Active option has no prior settlement")
                points_pnl += float(old_row["open"]) - prior_option_settle
                side_count += 1
            active_contract = None
            prior_option_settle = None
            if desired_on:
                new_contract = str(decision.put_contract)
                new_row = option_lookup.loc[(new_contract, day)]
                if is_initial_exception:
                    entry_price = float(new_row["settle"])
                else:
                    entry_price = float(new_row["open"])
                    points_pnl += float(new_row["settle"]) - entry_price
                side_count += 1
                active_contract = new_contract
                prior_option_settle = float(new_row["settle"])
            daily.loc[day_index, f"{label}_put_cost_rate"] = side_count * PUT_ONE_WAY_COST
        elif active_contract is not None:
            current_row = option_lookup.loc[(active_contract, day)]
            if prior_option_settle is None:
                raise RuntimeError("Held option has no prior settlement")
            points_pnl += float(current_row["settle"]) - prior_option_settle
            prior_option_settle = float(current_row["settle"])

        daily.loc[day_index, f"{label}_put_pnl_ret"] = points_pnl / denominator
        if active_contract is not None:
            mark = option_lookup.loc[(active_contract, day)]
            daily.loc[day_index, f"{label}_put_contract"] = active_contract
            daily.loc[day_index, f"{label}_put_settle"] = float(mark["settle"])
            daily.loc[day_index, f"{label}_signal_on"] = True
    return daily


def assemble_daily(
    upstream: pd.DataFrame,
    always_overlay: pd.DataFrame,
    gated_overlay: pd.DataFrame,
) -> pd.DataFrame:
    daily = upstream.copy().sort_values("date").reset_index(drop=True)
    for overlay, label in [(always_overlay, "immediate_always"), (gated_overlay, "immediate_gated")]:
        columns = [
            "date",
            f"{label}_put_pnl_ret",
            f"{label}_put_cost_rate",
            f"{label}_put_contract",
            f"{label}_put_settle",
            f"{label}_signal_on",
        ]
        daily = daily.merge(overlay[columns], on="date", how="left", validate="one_to_one")

    daily["no_put_net_ret"] = daily["baseline_net_ret"]
    daily["legacy_always_put_net_ret"] = daily["protected_net_ret"]
    for label in ["immediate_always", "immediate_gated"]:
        daily[f"{label}_gross_ret"] = daily["im_gross_ret"] + daily[f"{label}_put_pnl_ret"]
        daily[f"{label}_net_ret"] = (
            (1.0 + daily[f"{label}_gross_ret"])
            * (1.0 - daily["cost_rate"])
            * (1.0 - daily[f"{label}_put_cost_rate"])
            - 1.0
        )
        daily[f"{label}_cash_weight"] = CASH_WEIGHT
        active = daily[f"{label}_put_contract"].ne("")
        daily.loc[active, f"{label}_cash_weight"] = (
            CASH_WEIGHT
            - daily.loc[active, f"{label}_put_settle"] / daily.loc[active, "settle"]
        ).clip(lower=0.0)
        daily[f"{label}_plus_cash_ret"] = (
            daily[f"{label}_net_ret"] + daily[f"{label}_cash_weight"] * CASH_DAILY_RETURN
        )
    daily["no_put_plus_cash_ret"] = daily["baseline_plus_cash_ret"]
    daily["legacy_always_put_plus_cash_ret"] = daily["protected_plus_cash_ret"]

    return_columns = [
        "no_put_net_ret",
        "legacy_always_put_net_ret",
        "immediate_always_net_ret",
        "immediate_gated_net_ret",
        "no_put_plus_cash_ret",
        "legacy_always_put_plus_cash_ret",
        "immediate_always_plus_cash_ret",
        "immediate_gated_plus_cash_ret",
    ]
    for column in return_columns:
        daily[f"nav_{column.removesuffix('_ret')}"] = (1.0 + daily[column]).cumprod()
    if daily[return_columns].isna().any().any() or (daily[return_columns] <= -1.0).any().any():
        raise RuntimeError("Invalid v2 daily return series")
    return daily


def metric_from_returns(returns: pd.Series) -> dict[str, float]:
    clean = returns.astype(float).dropna()
    if clean.empty:
        return {
            "total_return": np.nan,
            "cagr": np.nan,
            "max_drawdown": np.nan,
            "annual_volatility": np.nan,
            "sharpe_0rf": np.nan,
        }
    nav = pd.concat([pd.Series([1.0]), (1.0 + clean.reset_index(drop=True)).cumprod()], ignore_index=True)
    volatility = float(clean.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(clean) > 1 else np.nan
    sharpe = float(clean.mean() / clean.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(clean) > 1 and clean.std(ddof=1) > 0 else np.nan
    return {
        "total_return": float(nav.iloc[-1] - 1.0),
        "cagr": float(nav.iloc[-1] ** (TRADING_DAYS / len(clean)) - 1.0),
        "max_drawdown": float((nav / nav.cummax() - 1.0).min()),
        "annual_volatility": volatility,
        "sharpe_0rf": sharpe,
    }


def comparison_metrics(subset: pd.DataFrame) -> dict[str, float]:
    columns = {
        "no_put": "no_put_net_ret",
        "legacy_always": "legacy_always_put_net_ret",
        "immediate_always": "immediate_always_net_ret",
        "immediate_gated": "immediate_gated_net_ret",
        "no_put_cash": "no_put_plus_cash_ret",
        "immediate_always_cash": "immediate_always_plus_cash_ret",
        "immediate_gated_cash": "immediate_gated_plus_cash_ret",
    }
    values = {label: metric_from_returns(subset[column]) for label, column in columns.items()}
    result: dict[str, float] = {}
    for label, metrics in values.items():
        result[f"{label}_cagr"] = metrics["cagr"]
        result[f"{label}_max_drawdown"] = metrics["max_drawdown"]
    result.update(
        {
            "gated_return_delta_vs_no_put_pp": (values["immediate_gated"]["cagr"] - values["no_put"]["cagr"]) * 100.0,
            "gated_drawdown_improvement_vs_no_put_pp": (
                values["immediate_gated"]["max_drawdown"] - values["no_put"]["max_drawdown"]
            ) * 100.0,
            "gated_return_delta_vs_immediate_always_pp": (
                values["immediate_gated"]["cagr"] - values["immediate_always"]["cagr"]
            ) * 100.0,
            "gated_drawdown_delta_vs_immediate_always_pp": (
                values["immediate_gated"]["max_drawdown"] - values["immediate_always"]["max_drawdown"]
            ) * 100.0,
            "gated_return_delta_vs_legacy_v1_pp": (
                values["immediate_gated"]["cagr"] - values["legacy_always"]["cagr"]
            ) * 100.0,
            "gated_drawdown_delta_vs_legacy_v1_pp": (
                values["immediate_gated"]["max_drawdown"] - values["legacy_always"]["max_drawdown"]
            ) * 100.0,
            "immediate_gated_total_return": values["immediate_gated"]["total_return"],
            "immediate_gated_annual_volatility": values["immediate_gated"]["annual_volatility"],
            "immediate_gated_sharpe_0rf": values["immediate_gated"]["sharpe_0rf"],
        }
    )
    return result


def metrics_by_window(daily: pd.DataFrame) -> pd.DataFrame:
    start = pd.Timestamp(daily["date"].min())
    end = pd.Timestamp(daily["date"].max())
    windows = [
        ("full", start),
        ("10y", end - pd.DateOffset(years=10)),
        ("5y", end - pd.DateOffset(years=5)),
        ("3y", end - pd.DateOffset(years=3)),
        ("1y", end - pd.DateOffset(years=1)),
    ]
    rows = []
    for name, cutoff in windows:
        available = name == "full" or start <= cutoff
        subset = daily[daily["date"] >= cutoff].copy() if available else daily.iloc[0:0].copy()
        row: dict[str, object] = {
            "window": name,
            "available": available,
            "unavailable_reason": "" if available else f"IM/MO history starts {start.date()}, shorter than {name}",
            "requested_start": cutoff.date().isoformat(),
            "actual_start": subset["date"].min().date().isoformat() if available else "",
            "end": end.date().isoformat(),
            "trading_days": int(len(subset)),
        }
        row.update(comparison_metrics(subset))
        rows.append(row)
    return pd.DataFrame(rows)


def annual_metrics(daily: pd.DataFrame) -> pd.DataFrame:
    start, end = pd.Timestamp(daily["date"].min()), pd.Timestamp(daily["date"].max())
    rows = []
    for year, subset in daily.groupby(daily["date"].dt.year, sort=True):
        row: dict[str, object] = {
            "year": int(year),
            "partial_year": int(year) in {start.year, end.year},
            "period_start": subset["date"].min().date().isoformat(),
            "period_end": subset["date"].max().date().isoformat(),
            "trading_days": int(len(subset)),
        }
        row.update(comparison_metrics(subset))
        rows.append(row)
    return pd.DataFrame(rows)


def drawdown_episode(daily: pd.DataFrame, return_column: str) -> dict[str, object]:
    nav = (1.0 + daily[return_column]).cumprod()
    peaks = nav.cummax()
    drawdown = nav / peaks - 1.0
    trough_idx = int(drawdown.idxmin())
    peak_idx = int(nav.loc[:trough_idx].idxmax())
    recovered = daily.index[(daily.index > trough_idx) & (nav >= float(peaks.iloc[trough_idx]))]
    recovery_idx = int(recovered[0]) if len(recovered) else None
    return {
        "series": return_column,
        "peak_date": daily.loc[peak_idx, "date"].date().isoformat(),
        "trough_date": daily.loc[trough_idx, "date"].date().isoformat(),
        "recovery_date": daily.loc[recovery_idx, "date"].date().isoformat() if recovery_idx is not None else "unrecovered",
        "max_drawdown": float(drawdown.iloc[trough_idx]),
    }


def build_current_and_forecast(
    states: pd.DataFrame,
    tri: pd.DataFrame,
    decisions: pd.DataFrame,
    daily: pd.DataFrame,
    options: pd.DataFrame,
    forecasts: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    current_date = pd.Timestamp(states["date"].max())
    current_signal, current_analogues = walk_forward_forecast(
        states, tri, current_date, "current_2026-08-14_v2"
    )
    frozen_summary = pd.read_csv(VALUATION_V3_OUTPUT / "forward_valuation_summary.csv")
    frozen_median = float(
        frozen_summary.loc[
            frozen_summary["product"].eq("IM") & frozen_summary["horizon_years"].eq(3),
            "median_annualized",
        ].iloc[0]
    )
    if abs(float(current_signal["forecast_3y_median"]) - frozen_median) > 1e-12:
        raise RuntimeError("Current v2 signal does not reconcile to valuation v3")
    last_decision = decisions.iloc[-1]
    last_daily = daily.iloc[-1]
    historical_on = bool(last_decision["signal_on"])
    current_on = bool(current_signal["signal_on"])
    if current_on == historical_on:
        pending_action = "no_state_change_hold_existing_position"
    elif current_on:
        pending_action = "prospective_buy_next_trading_day_price_unknown"
    else:
        pending_action = "prospective_sell_next_trading_day_price_unknown"
    active_contract = str(last_daily["immediate_gated_put_contract"])
    active_row = options[
        options["date"].eq(END_DATE) & options["contract"].eq(active_contract)
    ]
    current_mark = float(active_row.iloc[0]["settle"]) if not active_row.empty else np.nan
    active_strike = float(MO_RE.fullmatch(active_contract).group("strike")) if active_contract else np.nan
    current_signal_frame = pd.DataFrame(
        [
            {
                **current_signal,
                "prior_month_state_date": pd.Timestamp(last_decision["state_date"]),
                "prior_month_signal_on": historical_on,
                "pending_action": pending_action,
                "current_active_put": active_contract,
                "current_active_put_strike": active_strike,
                "current_active_put_settle": current_mark,
                "current_im_contract": last_daily["contract"],
                "current_im_settle": float(last_daily["settle"]),
                "current_index_close": float(last_daily["csi1000_price_close"]),
                "current_put_strike_vs_index": active_strike / float(last_daily["csi1000_price_close"]) - 1.0,
                "current_put_mark_to_im_notional": current_mark / float(last_daily["settle"]),
            }
        ]
    )

    baseline_factor = float((1.0 + daily["no_put_plus_cash_ret"]).prod())
    candidate_factor = float((1.0 + daily["immediate_gated_plus_cash_ret"]).prod())
    overlay_relative_cagr = float(
        (candidate_factor / baseline_factor) ** (TRADING_DAYS / len(daily)) - 1.0
    )
    forecast = forecasts[forecasts["product"].eq("IM")].copy()
    forecast["historical_v2_overlay_relative_cagr"] = overlay_relative_cagr
    forecast["cost_adjusted_v2_annualized"] = (
        (1.0 + forecast["combined_annualized"]) * (1.0 + overlay_relative_cagr) - 1.0
    )
    forecast["cost_adjusted_v2_cumulative"] = (
        (1.0 + forecast["cost_adjusted_v2_annualized"]) ** forecast["horizon_years"] - 1.0
    )
    forecast["protected_projected_max_drawdown"] = np.nan
    forecast["adjustment_scope"] = "historical average v2 insurance cost only; future put payoff excluded"
    return current_signal_frame, current_analogues, forecast


def validate_selected_pre_settle(
    decisions: pd.DataFrame,
    options: pd.DataFrame,
    daily: pd.DataFrame,
) -> dict[str, object]:
    selected_parts = []
    for row in decisions.itertuples(index=False):
        held = options[
            options["contract"].eq(row.put_contract)
            & options["date"].between(row.execution_date, row.next_execution_date)
        ].copy()
        selected_parts.append(held)
    selected = pd.concat(selected_parts, ignore_index=True).drop_duplicates(["contract", "date"])
    selected = selected.sort_values(["contract", "date"])
    selected["prior_observed_settle"] = selected.groupby("contract")["settle"].shift(1)
    comparable = selected["prior_observed_settle"].notna() & selected["pre_settle"].notna()
    differences = (
        selected.loc[comparable, "pre_settle"]
        - selected.loc[comparable, "prior_observed_settle"]
    ).abs()
    return {
        "selected_rows": int(len(selected)),
        "comparable_rows": int(comparable.sum()),
        "mismatch_rows": int((differences > 1e-8).sum()),
        "max_abs_difference": float(differences.max()) if len(differences) else 0.0,
    }


def pct(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value:.2%}"


def pp(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value:+.2f}pp"


def render_window_table(metrics: pd.DataFrame) -> str:
    lines = [
        "| 窗口 | 无Put CAGR / MaxDD | 同执行永久Put CAGR / MaxDD | 即时估值Put v2 CAGR / MaxDD | v2相对无Put：收益 / 回撤 |",
        "|---|---:|---:|---:|---:|",
    ]
    for window in ["full", "10y", "5y", "3y", "1y"]:
        row = metrics[metrics["window"].eq(window)].iloc[0]
        if not bool(row["available"]):
            lines.append(f"| {window} | N/A | N/A | N/A | N/A |")
            continue
        lines.append(
            f"| {window} | {pct(row['no_put_cagr'])} / {pct(row['no_put_max_drawdown'])} | "
            f"{pct(row['immediate_always_cagr'])} / {pct(row['immediate_always_max_drawdown'])} | "
            f"{pct(row['immediate_gated_cagr'])} / {pct(row['immediate_gated_max_drawdown'])} | "
            f"{pp(row['gated_return_delta_vs_no_put_pp'])} / "
            f"{pp(row['gated_drawdown_improvement_vs_no_put_pp'])} |"
        )
    return "\n".join(lines)


def render_annual_table(annual: pd.DataFrame) -> str:
    lines = [
        "| 年份 | 无Put CAGR / MaxDD | 同执行永久Put CAGR / MaxDD | 即时估值Put v2 CAGR / MaxDD | v2相对无Put：收益 / 回撤 |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in annual.itertuples(index=False):
        year = f"{row.year}（部分）" if row.partial_year else str(row.year)
        lines.append(
            f"| {year} | {pct(row.no_put_cagr)} / {pct(row.no_put_max_drawdown)} | "
            f"{pct(row.immediate_always_cagr)} / {pct(row.immediate_always_max_drawdown)} | "
            f"{pct(row.immediate_gated_cagr)} / {pct(row.immediate_gated_max_drawdown)} | "
            f"{pp(row.gated_return_delta_vs_no_put_pp)} / "
            f"{pp(row.gated_drawdown_improvement_vs_no_put_pp)} |"
        )
    return "\n".join(lines)


def render_forecast_table(forecast: pd.DataFrame) -> str:
    order = {"悲观": 0, "中等": 1, "乐观": 2}
    rows = forecast.assign(_order=forecast["scenario"].map(order)).sort_values(["horizon_years", "_order"])
    lines = [
        "| 期限 | 情景 | 原组合年化 / 累计 | 只扣v2历史保险成本后年化 / 累计 | 保护后MaxDD |",
        "|---:|---|---:|---:|---:|",
    ]
    for row in rows.itertuples(index=False):
        lines.append(
            f"| {row.horizon_years}年 | {row.scenario} | {pct(row.combined_annualized)} / {pct(row.combined_cumulative)} | "
            f"{pct(row.cost_adjusted_v2_annualized)} / {pct(row.cost_adjusted_v2_cumulative)} | N/A |"
        )
    return "\n".join(lines)


def write_record(
    output_dir: Path,
    daily: pd.DataFrame,
    metrics: pd.DataFrame,
    annual: pd.DataFrame,
    decisions: pd.DataFrame,
    event_audit: pd.DataFrame,
    current: pd.DataFrame,
    forecast: pd.DataFrame,
    manifest: dict[str, object],
) -> None:
    full = metrics[metrics["window"].eq("full")].iloc[0]
    current_row = current.iloc[0]
    v1_metrics = pd.read_csv(GATED_V1_OUTPUT / "metrics_by_window.csv")
    v1_full = v1_metrics[v1_metrics["window"].eq("full")].iloc[0]
    on_count = int(decisions["signal_on"].sum())
    active_days = int(daily["immediate_gated_signal_on"].sum())
    if full["gated_drawdown_improvement_vs_no_put_pp"] >= 8.0:
        decision_text = "全样本回撤改善达到8个百分点宽容线，但仍需执行/流动性验证且未获实盘授权。"
    elif full["gated_return_delta_vs_no_put_pp"] < -1.0:
        decision_text = "回撤改善不足8个百分点且收益损失超过1个百分点，只能保留为观察/诊断。"
    else:
        decision_text = "未触发预注册否决，但本版仍仅为研究观察。"
    overlay_cost = float(forecast["historical_v2_overlay_relative_cagr"].iloc[0])
    jan22 = event_audit[event_audit["date"].eq(pd.Timestamp("2024-01-22"))].iloc[0]
    feb05 = event_audit[event_audit["date"].eq(pd.Timestamp("2024-02-05"))].iloc[0]
    record = f"""# IM 估值预测即时择时 + 近3个月最低执行价 Put v2：结果记录

运行日期：{date.today().isoformat()}  
研究状态：研究审计；未获准实盘  
正式样本：{daily['date'].min().date().isoformat()} 至 {daily['date'].max().date().isoformat()}  
核心修订：估值 `T` 日收盘确认，`T+1 open` 立即执行 Put，与 IM 换月解耦

## 结论摘要

- 无 Put 全样本净年化 {pct(full['no_put_cagr'])}、MaxDD {pct(full['no_put_max_drawdown'])}；同一即时月度执行表下永久 Put 为 {pct(full['immediate_always_cagr'])}/{pct(full['immediate_always_max_drawdown'])}；即时估值择时 v2 为 {pct(full['immediate_gated_cagr'])}/{pct(full['immediate_gated_max_drawdown'])}。
- v2 相对无 Put 年化变化 {pp(full['gated_return_delta_vs_no_put_pp'])}、回撤改善 {pp(full['gated_drawdown_improvement_vs_no_put_pp'])}；相对同执行永久 Put 年化变化 {pp(full['gated_return_delta_vs_immediate_always_pp'])}、MaxDD变化 {pp(full['gated_drawdown_delta_vs_immediate_always_pp'])}。{decision_text}
- v1 的延迟执行结果为 {pct(v1_full['gated_put_cagr'])}/{pct(v1_full['gated_put_max_drawdown'])}；v2 修正后相对 v1 年化变化 {pp((full['immediate_gated_cagr']-v1_full['gated_put_cagr'])*100)}、MaxDD改善 {pp((full['immediate_gated_max_drawdown']-v1_full['gated_put_max_drawdown'])*100)}。v1 不再用于用户假设结论。
- 50次历史月末决策中开启 {on_count} 次，保护日 {active_days}/{len(daily)}（{pct(active_days/len(daily))}）；所有新旧 Put 的执行日官方开盘价、成交量和持仓量均通过预注册检查。

## 强制窗口

{render_window_table(metrics)}

旧永久 Put v1 使用不同的 IM 到期同步换仓表，只作为历史参考：全样本 {pct(full['legacy_always_cagr'])}/{pct(full['legacy_always_max_drawdown'])}。

## 逐年结果

{render_annual_table(annual)}

## 2024执行修正核对

- 2023-12-29收盘估值预测转为负；v2 在2024-01-02开盘买入 `{event_audit.loc[event_audit['date'].eq(pd.Timestamp('2024-01-02')), 'immediate_gated_put_contract'].iloc[0]}`，而不是等待1月22日。
- 2024-01-22 IM净收益 {pct(jan22['no_put_net_ret'])}；v2 当日为 {pct(jan22['immediate_gated_net_ret'])}，Put损益缓冲 {pct(jan22['immediate_gated_put_pnl_ret'])}。
- 2024-01-31收盘的新估值状态转为不保护，v2 在2月1日开盘退出；因此2024-02-05 IM {pct(feb05['no_put_net_ret'])} 时 v2 已无 Put，当日为 {pct(feb05['immediate_gated_net_ret'])}。这不是时点错误，而是估值变便宜后立即撤保这一规则的真实后果。

## 当前信号与3年/5年情景

- 2026-08-14中证1000收盘 {current_row['current_index_close']:.2f}，3年类比中位数 {pct(current_row['forecast_3y_median'])}，当前信号 **{'ON' if current_row['signal_on'] else 'OFF'}**。
- 2026-07-31上一次正式月末信号同为 **{'ON' if current_row['prior_month_signal_on'] else 'OFF'}**，因此当前动作是 `{current_row['pending_action']}`，不因中期审计重复换仓。
- 当前 v2 盯市 Put 为 `{current_row['current_active_put']}`，执行价相对指数 {pct(current_row['current_put_strike_vs_index'])}，结算权利金占 IM 名义 {pct(current_row['current_put_mark_to_im_notional'])}。
- 历史 v2 保险（含占用现金利息）相对无 Put+现金年化因子 {pct(overlay_cost)}。下表只扣这项历史平均成本，不预测未知下跌路径的 Put 赔付。

{render_forecast_table(forecast)}

## 数据、成本与完整性

- 真实中金所 IM/MO 官方行情；MO 主执行价为 `T+1 open`，日终盯市用官方结算价。每1份IM名义匹配2份MO Put。
- IM每边1bp；整组Put每次买/卖各1bp。70%现金年化3%，有Put时扣除权利金占用；不使用保证金放大。
- 目标到期日与 `T+3个月` 的实际距离为 {int(decisions['expiry_distance_days'].min())}—{int(decisions['expiry_distance_days'].max())} 天；所有入场成交量/持仓量为正。
- 被选 Put `pre_settle` 连续性可比 {manifest['pre_settle_check']['comparable_rows']} 行，不一致 {manifest['pre_settle_check']['mismatch_rows']} 行。
- 无Put曲线与冻结 IM/Put v1 基线逐日完全一致；旧永久参考也逐日完全一致。官方开盘价仍不保证能以不产生额外冲击的价格成交。

## 复现与状态

- 冻结规格：`docs/{VERSION}_spec.md`，SHA-256 `{manifest['spec_sha256']}`。
- 脚本：`{VERSION}.py`，SHA-256 `{manifest['script_sha256']}`。
- 命令：`{manifest['command']}`。
- 本版未获准实盘，不生成自动或人工订单。
"""
    (output_dir / "record.md").write_text(record, encoding="utf-8")


def run(output_dir: Path) -> None:
    if output_dir.exists():
        raise FileExistsError(f"Formal output exists and will not be overwritten: {output_dir}")
    spec_hash = verify_spec()
    input_paths = {
        "put_v1_daily": PUT_V1_OUTPUT / "daily_nav.csv",
        "options": PUT_V1_DATA / "cffex_mo_puts.csv",
        "gated_v1_metrics": GATED_V1_OUTPUT / "metrics_by_window.csv",
        "valuation_states": VALUATION_V3_OUTPUT / "monthly_valuation_state.csv",
        "tri": VALUATION_V3_DATA / "csindex_H00852.csv",
        "forecasts": VALUATION_V3_OUTPUT / "combined_forecasts.csv",
        "put_v1_manifest": PUT_V1_OUTPUT / "data_manifest.json",
        "valuation_v3_manifest": VALUATION_V3_OUTPUT / "data_manifest.json",
    }
    missing = [str(path) for path in input_paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing frozen upstream input: {missing}")

    upstream = pd.read_csv(input_paths["put_v1_daily"], parse_dates=["date"])
    options = add_option_expiry(pd.read_csv(input_paths["options"], parse_dates=["date"]))
    states = pd.read_csv(input_paths["valuation_states"], parse_dates=["date"])
    states = states[states["product"].eq("IM")].sort_values("date").reset_index(drop=True)
    tri = pd.read_csv(input_paths["tri"], parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    forecasts = pd.read_csv(input_paths["forecasts"])

    if upstream["date"].min() != START_DATE or upstream["date"].max() != END_DATE:
        raise RuntimeError("Frozen IM/MO daily sample dates changed")
    decisions, analogues = build_decision_schedule(states, tri, options, upstream)
    decisions["always_on"] = True
    always_overlay = build_option_overlay(upstream, decisions, options, "always_on", "immediate_always")
    gated_overlay = build_option_overlay(upstream, decisions, options, "signal_on", "immediate_gated")
    daily = assemble_daily(upstream, always_overlay, gated_overlay)

    metrics = metrics_by_window(daily)
    annual = annual_metrics(daily)
    current, current_analogues, forecast = build_current_and_forecast(
        states, tri, decisions, daily, options, forecasts
    )
    all_analogues = pd.concat([analogues, current_analogues], ignore_index=True)
    event_dates = pd.to_datetime([
        "2023-12-29",
        "2024-01-02",
        "2024-01-22",
        "2024-01-31",
        "2024-02-01",
        "2024-02-05",
    ])
    event_audit = daily[daily["date"].isin(event_dates)][[
        "date",
        "contract",
        "no_put_net_ret",
        "legacy_always_put_net_ret",
        "immediate_always_net_ret",
        "immediate_gated_net_ret",
        "immediate_gated_signal_on",
        "immediate_gated_put_contract",
        "immediate_gated_put_pnl_ret",
        "immediate_gated_put_cost_rate",
    ]].copy()
    worst_days = daily.nsmallest(5, "no_put_net_ret")[[
        "date",
        "contract",
        "no_put_net_ret",
        "legacy_always_put_net_ret",
        "immediate_always_net_ret",
        "immediate_gated_net_ret",
        "immediate_gated_signal_on",
        "immediate_gated_put_contract",
        "immediate_gated_put_pnl_ret",
    ]].copy()
    drawdowns = pd.DataFrame([
        drawdown_episode(daily, "no_put_net_ret"),
        drawdown_episode(daily, "legacy_always_put_net_ret"),
        drawdown_episode(daily, "immediate_always_net_ret"),
        drawdown_episode(daily, "immediate_gated_net_ret"),
        drawdown_episode(daily, "no_put_plus_cash_ret"),
        drawdown_episode(daily, "immediate_gated_plus_cash_ret"),
    ])
    pre_settle_check = validate_selected_pre_settle(decisions, options, daily)
    extremes = daily.loc[
        daily["immediate_gated_net_ret"].abs() > 0.10,
        ["date", "contract", "immediate_gated_put_contract", "immediate_gated_net_ret", "no_put_net_ret"],
    ].copy()

    baseline_diff = float((daily["no_put_net_ret"] - upstream["baseline_net_ret"]).abs().max())
    legacy_diff = float((daily["legacy_always_put_net_ret"] - upstream["protected_net_ret"]).abs().max())
    if baseline_diff > 0 or legacy_diff > 1e-18:
        raise RuntimeError(f"Frozen baseline mismatch: {baseline_diff}, {legacy_diff}")

    command = f"{Path(sys.executable).name} {Path(__file__).name}"
    manifest: dict[str, object] = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_status": "research_only_not_approved_for_live_trading",
        "command": command,
        "spec_sha256": spec_hash,
        "script_sha256": sha256_file(Path(__file__)),
        "inputs": {
            name: {"path": str(path.resolve()), "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for name, path in input_paths.items()
        },
        "sample": {
            "start": START_DATE.date().isoformat(),
            "end": END_DATE.date().isoformat(),
            "trading_days": int(len(daily)),
            "timezone": "Asia/Shanghai",
        },
        "execution": {
            "signal_timestamp": "valuation state T close",
            "regular_execution": "next common CFFEX trading day official open",
            "initial_exception": "2022-07-22 MO inception; enter put at settlement to align IM baseline",
            "expiry_selection": "listed expiry closest to state date plus 3 calendar months; later expiry wins exact tie",
            "strike_selection": "literal minimum strike in selected expiry",
            "put_quantity": "2 MO puts per 1 IM equivalent",
            "im_one_way_cost": PUT_ONE_WAY_COST,
            "put_basket_one_way_cost": PUT_ONE_WAY_COST,
        },
        "decisions": {
            "count": int(len(decisions)),
            "on_count": int(decisions["signal_on"].sum()),
            "active_days": int(daily["immediate_gated_signal_on"].sum()),
            "state_switches": int(decisions["signal_on"].astype(int).diff().abs().fillna(0).sum()),
            "gated_option_transaction_sides": int(round(float(daily["immediate_gated_put_cost_rate"].sum()) / PUT_ONE_WAY_COST)),
            "always_option_transaction_sides": int(round(float(daily["immediate_always_put_cost_rate"].sum()) / PUT_ONE_WAY_COST)),
            "min_expiry_distance_days": int(decisions["expiry_distance_days"].min()),
            "max_expiry_distance_days": int(decisions["expiry_distance_days"].max()),
            "min_entry_volume": float(decisions["entry_volume"].min()),
            "min_entry_open_interest": float(decisions["entry_open_interest"].min()),
            "min_exit_volume": float(decisions["next_decision_exit_volume"].dropna().min()),
            "min_exit_open_interest": float(decisions["next_decision_exit_open_interest"].dropna().min()),
        },
        "checks": {
            "all_regular_execution_dates_after_state": bool(
                (decisions.loc[~decisions["initial_listing_exception"], "execution_date"]
                 > decisions.loc[~decisions["initial_listing_exception"], "state_date"]).all()
            ),
            "all_analogue_outcomes_known": bool((analogues["forward_end_date"] <= analogues["as_of"]).all()),
            "baseline_max_abs_difference": baseline_diff,
            "legacy_always_max_abs_difference": legacy_diff,
            "extreme_return_count": int(len(extremes)),
        },
        "pre_settle_check": pre_settle_check,
        "current": current.iloc[0].to_dict(),
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    daily.to_csv(output_dir / "daily_nav.csv", index=False, encoding="utf-8-sig")
    metrics.to_csv(output_dir / "metrics_by_window.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(output_dir / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    decisions.to_csv(output_dir / "decision_schedule.csv", index=False, encoding="utf-8-sig")
    all_analogues.to_csv(output_dir / "signal_analogues.csv", index=False, encoding="utf-8-sig")
    event_audit.to_csv(output_dir / "event_audit_2024.csv", index=False, encoding="utf-8-sig")
    worst_days.to_csv(output_dir / "worst_days.csv", index=False, encoding="utf-8-sig")
    drawdowns.to_csv(output_dir / "drawdown_episodes.csv", index=False, encoding="utf-8-sig")
    current.to_csv(output_dir / "current_signal.csv", index=False, encoding="utf-8-sig")
    forecast.to_csv(output_dir / "current_forecast_with_protection_cost.csv", index=False, encoding="utf-8-sig")
    extremes.to_csv(output_dir / "extreme_returns.csv", index=False, encoding="utf-8-sig")
    (output_dir / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (output_dir / "command_log.txt").write_text(command + "\n", encoding="utf-8")
    write_record(output_dir, daily, metrics, annual, decisions, event_audit, current, forecast, manifest)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IM valuation-gated immediate T+1-open MO put v2")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(args.output_dir)
