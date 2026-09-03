from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import build_ic_im_mainline_v1_3_fixed_performance as fixed
import freeze_ic_im_system_mainlines_v2 as frozen
import im_grid160_put_carry_objective_scan_v23 as put_family
import im_mo_close_execution_v8 as engine


ROOT = Path(__file__).resolve().parent
VERSION = "im_v13_momentum_put_independent_replay_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
OUTPUT = ROOT / "outputs" / VERSION
FIXED_DAILY = ROOT / "outputs" / "ic_im_mainline_v1_3_fixed_performance_v5" / "im_daily.csv.gz"
SCALED_REFERENCE = (
    ROOT
    / "quant_param_scan_runs"
    / "20260903_ic_im_rolling_arbitrage_im_v1_3_fixed_performance_v5_im_put_coverage_scope_execution_timing_put_coverage_scope_timing"
    / "daily_outputs"
    / "coverage_candidates.csv.gz"
)
REAL_START = pd.Timestamp("2022-07-22")
REFERENCE_CAPITAL_MULTIPLIER = 4
PUT_VARIANT = "current_4tier_mom3"
WINDOWS = {
    "full": None,
    "last_10y": pd.DateOffset(years=10),
    "last_5y": pd.DateOffset(years=5),
    "last_3y": pd.DateOffset(years=3),
    "last_1y": pd.DateOffset(years=1),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def verify_inputs() -> str:
    expected = SPEC_HASH.read_text(encoding="utf-8").split()[0].lower()
    actual = sha256(SPEC)
    if actual != expected:
        raise RuntimeError(f"Specification hash mismatch: {actual} != {expected}")
    required = [
        FIXED_DAILY,
        SCALED_REFERENCE,
        fixed.IM_COMPONENTS,
        fixed.IM_TARGET,
        engine.v4.OPTIONS,
        engine.v5.IM_QUOTES,
    ]
    missing = [str(path) for path in required if not Path(path).is_file()]
    if missing:
        raise FileNotFoundError(missing)
    if OUTPUT.exists():
        raise FileExistsError(f"Immutable formal output exists: {OUTPUT}")
    return actual


def schedules_and_market():
    upstream, active_im, options, _source, _state, _thresholds, _frozen = frozen._im_source_data()
    states, state_audit = put_family.build_states()
    real_dates = pd.DatetimeIndex(upstream["date"])
    base = put_family.put_model.im_v12.build_momentum_schedule(
        states[PUT_VARIANT], PUT_VARIANT, real_dates, f"independent_{PUT_VARIANT}"
    )
    target = pd.read_csv(
        fixed.IM_TARGET,
        parse_dates=["date"],
        usecols=["date", "put_signal_target_qty", "momentum_execution_weight"],
    )
    signal_check = base.merge(
        target[["date", "put_signal_target_qty"]].rename(columns={"date": "eval_date"}),
        on="eval_date",
        how="left",
        validate="one_to_one",
    )
    if signal_check["put_signal_target_qty"].isna().any():
        raise RuntimeError("Missing v1.3 Put target on an evaluation date")
    target_error = float(
        (
            signal_check["binary_target_qty"].astype(float)
            - signal_check["put_signal_target_qty"].astype(float)
        ).abs().max()
    )
    if target_error > 1e-12:
        raise RuntimeError(f"Put target schedule mismatch: {target_error}")

    base = base.merge(
        target[["date", "momentum_execution_weight"]].rename(
            columns={"date": "execution_date"}
        ),
        on="execution_date",
        how="left",
        validate="one_to_one",
    )
    if base["momentum_execution_weight"].isna().any():
        raise RuntimeError("Missing momentum execution weight")
    if not set(base["momentum_execution_weight"].unique()).issubset({0.0, 0.5, 1.0}):
        raise RuntimeError("Unexpected v1.3 momentum weight")

    core = base.copy()
    core["binary_target_qty"] = 2 * core["binary_target_qty"].astype(int)
    core["three_tier_target_qty"] = core["binary_target_qty"]
    core["candidate"] = "core_put_4x_integer"

    momentum = base.copy()
    raw = (
        2.0
        * momentum["binary_target_qty"].astype(float)
        * momentum["momentum_execution_weight"].astype(float)
    )
    if not np.allclose(raw, np.rint(raw), atol=1e-12, rtol=0.0):
        raise RuntimeError("Momentum Put 4x target is not integer")
    momentum["binary_target_qty"] = np.rint(raw).astype(int)
    momentum["three_tier_target_qty"] = momentum["binary_target_qty"]
    momentum["candidate"] = "momentum_put_4x_integer"
    return upstream, active_im, options, core, momentum, state_audit, target_error


def scale_replay(
    daily: pd.DataFrame, trades: pd.DataFrame, lives: pd.DataFrame, sleeve: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    result = daily.copy()
    result["raw_4x_put_qty"] = 2.0 * result["put_fraction"].astype(float)
    for column in ("put_pnl_ret", "put_cost_rate", "put_mark_fraction", "put_fraction"):
        result[column] = result[column].astype(float) / REFERENCE_CAPITAL_MULTIPLIER
    result["put_qty_normalized"] = 2.0 * result["put_fraction"]
    result["sleeve"] = sleeve

    trade_result = trades.copy()
    trade_result["sleeve"] = sleeve
    for column in ("target_qty", "old_qty", "new_qty", "buy_qty", "sell_qty"):
        if column in trade_result:
            trade_result[f"{column}_normalized"] = (
                trade_result[column].astype(float) / REFERENCE_CAPITAL_MULTIPLIER
            )
    life_result = lives.copy()
    life_result["sleeve"] = sleeve
    return result, trade_result, life_result


def execute_ledgers():
    upstream, active_im, options, core_schedule, momentum_schedule, state_audit, target_error = (
        schedules_and_market()
    )
    core = engine.run_real_normal_close(
        upstream, options, active_im, core_schedule, "3m", 0.95, "core_put_4x_integer"
    )
    momentum = engine.run_real_normal_close(
        upstream,
        options,
        active_im,
        momentum_schedule,
        "3m",
        0.95,
        "momentum_put_4x_integer",
    )
    core_daily, core_trades, core_lives = scale_replay(*core, "core")
    mom_daily, mom_trades, mom_lives = scale_replay(*momentum, "momentum")
    schedules = pd.concat(
        [
            core_schedule.assign(sleeve="core"),
            momentum_schedule.assign(sleeve="momentum"),
        ],
        ignore_index=True,
    )
    trades = pd.concat([core_trades, mom_trades], ignore_index=True, sort=False)
    lives = pd.concat([core_lives, mom_lives], ignore_index=True, sort=False)
    return (
        upstream,
        active_im,
        options,
        core_daily,
        mom_daily,
        schedules,
        trades,
        lives,
        state_audit,
        target_error,
    )


def component_frame() -> pd.DataFrame:
    components = fixed._load_im_components()
    components = components[components["date"].ge(REAL_START)].copy()
    target = pd.read_csv(fixed.IM_TARGET, parse_dates=["date"])
    columns = ["date", "momentum_execution_weight", "grid_held_eod", "total_im_units"]
    frame = components.merge(target[columns], on="date", validate="one_to_one")
    return frame.sort_values("date").reset_index(drop=True)


def core_parity(core_daily: pd.DataFrame, frame: pd.DataFrame) -> dict[str, float | int]:
    joined = core_daily.merge(
        frame[
            [
                "date",
                "put_pnl_ret",
                "put_cost_rate",
                "put_mark_fraction",
                "put_fraction",
                "put_qty",
                "put_contract",
            ]
        ],
        on="date",
        suffixes=("_replay", "_parent"),
        validate="one_to_one",
    )
    checks = {
        "rows": int(len(joined)),
        "put_pnl_max_abs": float(
            (joined["put_pnl_ret_replay"] - 0.5 * joined["put_pnl_ret_parent"]).abs().max()
        ),
        "put_cost_max_abs": float(
            (joined["put_cost_rate_replay"] - 0.5 * joined["put_cost_rate_parent"]).abs().max()
        ),
        "put_mark_max_abs": float(
            (
                joined["put_mark_fraction_replay"]
                - 0.5 * joined["put_mark_fraction_parent"]
            ).abs().max()
        ),
        "put_qty_max_abs": float(
            (joined["put_qty_normalized"] - 0.5 * joined["put_qty"]).abs().max()
        ),
        "contract_mismatch_rows": int(
            (
                joined["put_contract_replay"].fillna("").astype(str)
                != joined["put_contract_parent"].fillna("").astype(str)
            ).sum()
        ),
    }
    if (
        max(
            checks["put_pnl_max_abs"],
            checks["put_cost_max_abs"],
            checks["put_mark_max_abs"],
            checks["put_qty_max_abs"],
        )
        > 1e-12
        or checks["contract_mismatch_rows"] != 0
    ):
        raise RuntimeError(f"Independent core replay parity failed: {checks}")
    return checks


def compose_curves(
    frame: pd.DataFrame, core_daily: pd.DataFrame, mom_daily: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, float]]:
    core = core_daily.add_prefix("core_").rename(columns={"core_date": "date"})
    mom = mom_daily.add_prefix("mom_").rename(columns={"mom_date": "date"})
    merged = frame.merge(core, on="date", validate="one_to_one").merge(
        mom, on="date", validate="one_to_one"
    )
    weight = merged["momentum_execution_weight"].astype(float)
    grid = merged["grid_held_eod"].astype(float)
    units = 0.5 + 0.5 * weight + grid
    turnover = weight.diff().abs()
    turnover.iloc[0] = abs(float(weight.iloc[0]))
    momentum_cost_full = (
        fixed.im_proxy.ONE_WAY_COST * turnover
        + 2.0
        * fixed.im_proxy.ONE_WAY_COST
        * weight
        * merged["roll_event"].astype(float)
    )
    base_gross = merged["base_gross_ret"].astype(float) + merged["base_basis_ret"].astype(float)
    overlay_gross = merged["overlay_gross_ret"].astype(float) + merged["overlay_basis_ret"].astype(float)
    futures_gross = (0.5 + 0.5 * weight) * base_gross + overlay_gross
    futures_cost = (
        0.5 * merged["base_futures_cost_rate"].astype(float)
        + 0.5 * momentum_cost_full
        + merged["overlay_cost_rate"].astype(float)
    )
    call_pnl = 0.5 * merged["call_pnl_ret"].astype(float)
    call_cost = 0.5 * merged["call_cost_rate"].astype(float)
    call_margin = 0.5 * merged["call_margin_fraction"].astype(float)

    rows: list[pd.DataFrame] = []
    for strategy, include_momentum in (
        ("core_only_independent_replay", False),
        ("core_plus_momentum_independent_replay", True),
    ):
        put_pnl = merged["core_put_pnl_ret"].astype(float)
        put_cost = merged["core_put_cost_rate"].astype(float)
        put_mark = merged["core_put_mark_fraction"].astype(float)
        put_qty = merged["core_put_qty_normalized"].astype(float)
        if include_momentum:
            put_pnl = put_pnl + merged["mom_put_pnl_ret"].astype(float)
            put_cost = put_cost + merged["mom_put_cost_rate"].astype(float)
            put_mark = put_mark + merged["mom_put_mark_fraction"].astype(float)
            put_qty = put_qty + merged["mom_put_qty_normalized"].astype(float)
        pre_cash = (
            (1.0 + futures_gross + put_pnl + call_pnl)
            * (1.0 - futures_cost)
            * (1.0 - put_cost)
            * (1.0 - call_cost)
            - 1.0
        )
        cash = 1.0 - fixed.im_proxy.MARGIN_BUFFER_RATE * units - put_mark - call_margin
        if cash.lt(-1e-12).any():
            raise RuntimeError(f"Negative cash in {strategy}: {cash.min()}")
        ret = pre_cash + cash * fixed.im_proxy.CASH_DAILY_RETURN
        result = pd.DataFrame(
            {
                "date": merged["date"],
                "strategy": strategy,
                "ret": ret,
                "cash_weight": cash,
                "total_im_units": units,
                "momentum_weight": weight,
                "put_pnl_ret": put_pnl,
                "put_cost_rate": put_cost,
                "put_mark_fraction": put_mark,
                "put_qty_normalized": put_qty,
            }
        )
        result["nav"] = (1.0 + result["ret"]).cumprod()
        result["drawdown"] = result["nav"] / result["nav"].cummax() - 1.0
        rows.append(result)
    curves = pd.concat(rows, ignore_index=True)
    baseline = curves[curves["strategy"].eq("core_only_independent_replay")].reset_index(drop=True)
    fixed_daily = pd.read_csv(FIXED_DAILY, parse_dates=["date"])
    fixed_daily = fixed_daily[fixed_daily["date"].ge(REAL_START)].reset_index(drop=True)
    if not baseline["date"].equals(fixed_daily["date"]):
        raise RuntimeError("v5 baseline date mismatch")
    parity = {
        "ret_max_abs": float((baseline["ret"] - fixed_daily["ret"]).abs().max()),
        "cash_max_abs": float(
            (baseline["cash_weight"] - fixed_daily["cash_weight"]).abs().max()
        ),
        "put_qty_max_abs": float(
            (baseline["put_qty_normalized"] - fixed_daily["put_qty_normalized"]).abs().max()
        ),
    }
    if max(parity.values()) > 1e-12:
        raise RuntimeError(f"v5 full-curve baseline parity failed: {parity}")
    return curves, parity


def rolling_worst(ret: pd.Series, days: int) -> float:
    return float(np.expm1(np.log1p(ret.astype(float)).rolling(days).sum().min()))


def metric_values(sample: pd.DataFrame) -> dict[str, float | int | str]:
    ret = sample["ret"].astype(float).reset_index(drop=True)
    nav = (1.0 + ret).cumprod()
    dd = nav / nav.cummax() - 1.0
    std = float(ret.std(ddof=1))
    trough = int(dd.idxmin())
    peak = int(nav.loc[:trough].idxmax())
    return {
        "rows": int(len(sample)),
        "start": sample["date"].iloc[0].date().isoformat(),
        "end": sample["date"].iloc[-1].date().isoformat(),
        "ann_return": float(nav.iloc[-1] ** (252.0 / len(ret)) - 1.0),
        "ann_vol": std * math.sqrt(252.0),
        "sharpe": float(ret.mean()) / std * math.sqrt(252.0),
        "max_dd": float(dd.min()),
        "worst_1d": float(ret.min()),
        "worst_5d": rolling_worst(ret, 5),
        "worst_20d": rolling_worst(ret, 20),
        "worst_60d": rolling_worst(ret, 60),
        "put_cost_total": float(sample["put_cost_rate"].sum()),
        "avg_put_qty": float(sample["put_qty_normalized"].mean()),
        "max_put_qty": float(sample["put_qty_normalized"].max()),
        "min_cash_weight": float(sample["cash_weight"].min()),
        "dd_peak": sample.iloc[peak]["date"].date().isoformat(),
        "dd_trough": sample.iloc[trough]["date"].date().isoformat(),
    }


def build_metrics(curves: pd.DataFrame) -> pd.DataFrame:
    end = curves["date"].max()
    rows: list[dict[str, object]] = []
    for strategy, block in curves.groupby("strategy", sort=False):
        block = block.sort_values("date").reset_index(drop=True)
        for window, offset in WINDOWS.items():
            requested = None if offset is None else end - offset
            coverage_start = block["date"].min()
            available = requested is None or coverage_start <= requested + pd.Timedelta(days=7)
            if not available:
                rows.append(
                    {
                        "strategy": strategy,
                        "window": window,
                        "available": False,
                        "reason": "real_IM_MO_history_shorter_than_requested_window",
                    }
                )
                continue
            sample = block if requested is None else block[block["date"].ge(requested)]
            sample = sample.iloc[1:].reset_index(drop=True)
            rows.append(
                {
                    "strategy": strategy,
                    "window": window,
                    "available": True,
                    "reason": "",
                    **metric_values(sample),
                }
            )
    return pd.DataFrame(rows)


def price_integrity(trades: pd.DataFrame, options: pd.DataFrame) -> dict[str, float | int]:
    lookup = options.set_index(["contract", "date"])
    errors: list[float] = []
    missing = 0
    bad_new_liquidity = 0
    for row in trades.itertuples(index=False):
        day = pd.Timestamp(row.actual_execution_date)
        for contract_field, price_field, is_new in (
            ("old_contract", "old_trade_price", False),
            ("new_contract", "new_trade_price", True),
        ):
            contract = str(getattr(row, contract_field) or "").strip()
            price = getattr(row, price_field)
            if not contract or pd.isna(price):
                continue
            key = (contract, day)
            if key not in lookup.index:
                missing += 1
                continue
            quote = lookup.loc[key]
            if isinstance(quote, pd.DataFrame):
                raise RuntimeError(f"Duplicate raw option quote: {key}")
            errors.append(abs(float(price) - float(quote["close"])))
            if is_new and (float(quote["volume"]) <= 0 or float(quote["open_interest"]) <= 0):
                bad_new_liquidity += 1
    return {
        "checked_trade_legs": int(len(errors)),
        "missing_raw_quote_legs": missing,
        "bad_new_liquidity_legs": bad_new_liquidity,
        "max_close_price_error": max(errors, default=0.0),
    }


def decision(metrics: pd.DataFrame) -> tuple[dict[str, bool | float], str]:
    full = metrics[metrics["window"].eq("full")].set_index("strategy")
    base = full.loc["core_only_independent_replay"]
    candidate = full.loc["core_plus_momentum_independent_replay"]
    checks: dict[str, bool | float] = {
        "ann_return_delta": float(candidate.ann_return - base.ann_return),
        "max_dd_improvement": float(candidate.max_dd - base.max_dd),
        "sharpe_delta": float(candidate.sharpe - base.sharpe),
        "worst_20d_delta": float(candidate.worst_20d - base.worst_20d),
        "worst_60d_delta": float(candidate.worst_60d - base.worst_60d),
        "dd_improvement_ge_1pp": bool(candidate.max_dd - base.max_dd >= 0.01 - 1e-12),
        "cagr_loss_le_0_5pp": bool(candidate.ann_return - base.ann_return >= -0.005 - 1e-12),
        "sharpe_not_lower": bool(candidate.sharpe >= base.sharpe - 1e-12),
        "worst_20d_not_worse": bool(candidate.worst_20d >= base.worst_20d - 1e-12),
        "worst_60d_not_worse": bool(candidate.worst_60d >= base.worst_60d - 1e-12),
        "cash_nonnegative": bool(candidate.min_cash_weight >= -1e-12),
    }
    passed = all(value for key, value in checks.items() if isinstance(value, bool))
    checks["all_preregistered_checks_pass"] = passed
    label = (
        "independent_replay_supports_momentum_put_research_only_not_live_approved"
        if passed
        else "independent_replay_does_not_support_momentum_put_keep_current_research_only"
    )
    return checks, label


def record_text(
    metrics: pd.DataFrame,
    checks: dict[str, bool | float],
    decision_label: str,
    core_checks: dict[str, float | int],
    curve_parity: dict[str, float],
    prices: dict[str, float | int],
    trades: pd.DataFrame,
) -> str:
    available = metrics[metrics["available"].eq(True)].copy()
    labels = {
        "core_only_independent_replay": "仅核心仓Put",
        "core_plus_momentum_independent_replay": "核心仓+动量仓独立Put",
    }
    lines = [
        "|路径|真实全样本 CAGR / MaxDD|近3年|近1年|",
        "|---|---:|---:|---:|",
    ]
    for strategy, label in labels.items():
        block = available[available["strategy"].eq(strategy)].set_index("window")
        cells = []
        for window in ("full", "last_3y", "last_1y"):
            row = block.loc[window]
            cells.append(f"{row.ann_return:.2%} / {row.max_dd:.2%}")
        lines.append(f"|{label}|" + "|".join(cells) + "|")
    momentum_trades = trades[trades["sleeve"].eq("momentum")]
    delayed = momentum_trades[
        pd.to_datetime(momentum_trades["actual_execution_date"])
        > pd.to_datetime(momentum_trades["scheduled_execution_date"])
    ]
    return f"""# IM v1.3 动量仓 Put 独立交易账本重放 v1

状态：`research_only_not_live_approved`；不修改冻结V2、v1.3研究信号、Poe、账本或交易配置。

## 结论

`{decision_label}`。

{chr(10).join(lines)}

- 近5年：N/A；真实MO历史不足5年。
- 近10年：N/A；真实MO历史不足10年。
- CAGR变化：{float(checks['ann_return_delta']):+.2%}；最大回撤改善：{float(checks['max_dd_improvement']):+.2%}；Sharpe变化：{float(checks['sharpe_delta']):+.3f}。

## 独立执行方法

- 原始MO Put共123,158行，真实区间2022-07-22至2026-08-14；使用官方收盘成交与官方结算盯市。
- 核心与动量是两个独立账本。动量仓从空仓重新进入时会按当日IM收盘重新选择最接近95%行权价的MO，而不是复制核心仓旧合约。
- 在4倍参考资本运行整数张数，随后除以4归一化；每归一化合约单边成本0.005%。
- 动量账本交易事件{len(momentum_trades)}个，延迟成交事件{len(delayed)}个。

## 强制校验

- 核心Put逐日重放：损益误差`{core_checks['put_pnl_max_abs']:.3e}`，成本误差`{core_checks['put_cost_max_abs']:.3e}`，市值误差`{core_checks['put_mark_max_abs']:.3e}`，合约不一致{core_checks['contract_mismatch_rows']}行。
- 当前v5完整组合收益复现误差`{curve_parity['ret_max_abs']:.3e}`。
- 交易价回查：{prices['checked_trade_legs']}条腿；缺失报价{prices['missing_raw_quote_legs']}；新开仓无有效成交量/持仓量{prices['bad_new_liquidity_legs']}；最大收盘价误差`{prices['max_close_price_error']:.3e}`。

## 成本与限制

- 已计期货、Put、Call既有成本和3%现金收益；每1倍IM按30%保证金/缓冲。
- 未计买卖价差、市场冲击、收盘容量、涨跌停、动态保证金和用户实际账户整数映射。
- 15%保证金仅是用户提供的操作上限，本研究未用经纪商结算单验证。

## 证据文件

- `metrics_by_window.csv`、`daily_curves.csv.gz`、`put_daily_ledgers.csv.gz`。
- `put_trades.csv`、`put_lifecycles.csv`、`put_schedules.csv.gz`。
- `validation.json`、`run_manifest.json`。
"""


def main() -> None:
    spec_hash = verify_inputs()
    (
        upstream,
        active_im,
        options,
        core_daily,
        mom_daily,
        schedules,
        trades,
        lives,
        state_audit,
        target_error,
    ) = execute_ledgers()
    frame = component_frame()
    core_checks = core_parity(core_daily, frame)
    curves, curve_parity = compose_curves(frame, core_daily, mom_daily)
    metrics = build_metrics(curves)
    checks, decision_label = decision(metrics)
    prices = price_integrity(trades, options)
    if (
        prices["missing_raw_quote_legs"] != 0
        or prices["bad_new_liquidity_legs"] != 0
        or prices["max_close_price_error"] > 1e-12
    ):
        raise RuntimeError(f"Trade price integrity failed: {prices}")

    scaled = pd.read_csv(SCALED_REFERENCE, parse_dates=["date"], low_memory=False)
    independent = curves[
        curves["strategy"].eq("core_plus_momentum_independent_replay")
    ][["date", "ret"]]
    reference = scaled[scaled["candidate"].eq("core_plus_momentum")][["date", "ret"]]
    approximation = independent.merge(
        reference, on="date", suffixes=("_independent", "_scaled"), validate="one_to_one"
    )
    approximation["daily_return_delta"] = (
        approximation["ret_independent"] - approximation["ret_scaled"]
    )

    validation = {
        "status": "research_only_not_live_approved",
        "spec_sha256": spec_hash,
        "put_target_schedule_max_abs_error": target_error,
        "core_replay_parity": core_checks,
        "full_curve_v5_parity": curve_parity,
        "price_integrity": prices,
        "raw_mo_rows": int(len(options)),
        "raw_mo_start": options["date"].min().date().isoformat(),
        "raw_mo_end": options["date"].max().date().isoformat(),
        "im_rows": int(len(upstream)),
        "state_audit": state_audit,
        "decision_checks": checks,
        "scaled_approximation_daily_max_abs": float(
            approximation["daily_return_delta"].abs().max()
        ),
    }
    OUTPUT.mkdir(parents=True)
    curves.to_csv(OUTPUT / "daily_curves.csv.gz", index=False, compression="gzip")
    pd.concat([core_daily, mom_daily], ignore_index=True).to_csv(
        OUTPUT / "put_daily_ledgers.csv.gz", index=False, compression="gzip"
    )
    schedules.to_csv(OUTPUT / "put_schedules.csv.gz", index=False, compression="gzip")
    trades.to_csv(OUTPUT / "put_trades.csv", index=False)
    lives.to_csv(OUTPUT / "put_lifecycles.csv", index=False)
    metrics.to_csv(OUTPUT / "metrics_by_window.csv", index=False)
    approximation.to_csv(OUTPUT / "scaled_approximation_comparison.csv", index=False)
    (OUTPUT / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "version": VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "research_only_not_live_approved",
        "decision": decision_label,
        "spec_sha256": spec_hash,
        "script_sha256": sha256(Path(__file__)),
        "source_hashes": {
            str(Path(path).relative_to(ROOT)): sha256(Path(path))
            for path in (
                SPEC,
                SPEC_HASH,
                FIXED_DAILY,
                fixed.IM_COMPONENTS,
                fixed.IM_TARGET,
                engine.v4.OPTIONS,
                engine.v5.IM_QUOTES,
            )
        },
        "git_commit": git_value("rev-parse", "HEAD"),
        "git_status": git_value("status", "--short"),
        "command": f"python -X utf8 {Path(__file__).name}",
    }
    (OUTPUT / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "record.md").write_text(
        record_text(metrics, checks, decision_label, core_checks, curve_parity, prices, trades),
        encoding="utf-8",
    )
    (OUTPUT / "command_log.txt").write_text(manifest["command"] + "\n", encoding="utf-8")
    print(metrics.to_string(index=False))
    print(json.dumps(validation, ensure_ascii=False, indent=2, default=str))
    print(f"Output: {OUTPUT}")


if __name__ == "__main__":
    main()
