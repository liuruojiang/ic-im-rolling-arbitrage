"""Exposure-normalized futures attribution and fixed-core index/basis split.

Research-only diagnostic for the current v1.3-r7 historical replay.  It does
not alter strategy logic, target schedules, or any production/live state.
"""
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import analyze_v13_component_attribution_20260912 as base


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "v13_exposure_and_fixed_core_basis_20260912"
PRICE_INDEX = {
    "IC": ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_000905.csv",
    "IM": ROOT / "data" / "ic_im_valuation_risk_premium_forecast_v3" / "csindex_000852.csv",
}
SIGNALS = ROOT / "outputs" / "nav_r7_complete_refresh_20260911" / "historical_signals.json"
PLAYERS = (
    "core_index_growth",
    "core_basis_excess",
    "core_futures_cost",
    "momentum",
    "grid",
    "long_put",
    "short_call",
    "cash",
)
CORE_PLAYERS = PLAYERS[:3]
GROUPS = (CORE_PLAYERS,) + tuple((name,) for name in PLAYERS[3:])
PLAYER_BIT = {name: 1 << i for i, name in enumerate(PLAYERS)}
FACTORIAL = math.factorial


def _load_tail_index_prices(product: str) -> pd.Series:
    raw = json.loads(SIGNALS.read_text(encoding="utf-8"))
    values = {
        pd.Timestamp(day): float(payload[product]["index_price"])
        for day, payload in raw.items()
    }
    return pd.Series(values, name="close").sort_index()


def _index_returns(product: str, dates: pd.Series) -> pd.Series:
    """Return the price-index daily return on exactly the replay calendar."""
    historical = pd.read_csv(PRICE_INDEX[product], parse_dates=["date"])[["date", "close"]]
    historical = historical.drop_duplicates("date", keep="last").set_index("date")["close"].astype(float)
    tail = _load_tail_index_prices(product)
    close = pd.concat([historical, tail[~tail.index.isin(historical.index)]]).sort_index()
    returns = close.pct_change()
    wanted = pd.DatetimeIndex(pd.to_datetime(dates))
    result = returns.reindex(wanted)
    if result.isna().any():
        missing = wanted[result.isna()].strftime("%Y-%m-%d").tolist()[:3]
        raise RuntimeError(f"{product} missing price-index return: {missing}")
    return pd.Series(result.to_numpy(dtype=float), index=dates.index)


def build_product(product: str) -> pd.DataFrame:
    formal = base.load_ic_formal() if product == "IC" else base.load_im_formal()
    tail = base.load_tail(product)
    formal = formal.assign(source_segment="formal")
    tail = tail.assign(source_segment="r7_tail")
    frame = pd.concat([formal, tail], ignore_index=True).sort_values("date").reset_index(drop=True)
    if frame["date"].duplicated().any():
        raise RuntimeError(f"{product}: duplicate dates")
    index_ret = _index_returns(product, frame["date"])
    # The first replay row represents portfolio initialization, not an overnight
    # P&L.  Match the official fixed curve's zero first-day futures return.
    index_ret.iloc[0] = 0.0
    frame["core_index_ret"] = index_ret.to_numpy()
    is_ic_formal = frame["formula"].eq("ic_formal")
    is_tail = frame["source_segment"].eq("r7_tail")
    future_unit = frame["futures_gross_ret"].astype(float).copy()
    if is_tail.any():
        future_unit.loc[is_tail] = (
            frame.loc[is_tail, "futures_gross_ret"].astype(float)
            / frame.loc[is_tail, "total_units"].astype(float)
        )
    if product == "IM":
        formal_mask = ~is_tail
        future_unit.loc[formal_mask] = (
            frame.loc[formal_mask, "base_gross_ret"].astype(float)
            + frame.loc[formal_mask, "base_basis_ret"].astype(float)
        )
    frame["core_future_unit_ret"] = future_unit
    # Additive residual preserves the daily futures return exactly.  Its factor
    # form is also retained to disclose that this is a relative-price excess, not
    # a static quoted discount.
    frame["core_basis_ret"] = frame["core_future_unit_ret"] - frame["core_index_ret"]
    frame["core_basis_factor_ret"] = (
        (1.0 + frame["core_future_unit_ret"]) / (1.0 + frame["core_index_ret"]) - 1.0
    )
    frame["core_index_pnl"] = 0.5 * frame["core_index_ret"]
    frame["core_basis_pnl"] = 0.5 * frame["core_basis_ret"]
    frame["core_cost_pnl"] = 0.0
    # IC's checkpoint puts its core futures cost inside the core sleeve before
    # combining it with Put, grid and cash.  Split it as an explicit negative
    # P&L so child contributions add exactly to the former fixed-F contribution.
    if is_ic_formal.any():
        initial = frame.index.to_series().eq(frame.index[0]).astype(float)
        core_cost_full = base.ONE_WAY * initial + 2.0 * base.ONE_WAY * frame["roll_event"].astype(float)
        core_net_unit = (1.0 + frame["core_future_unit_ret"]) * (1.0 - core_cost_full) - 1.0
        frame.loc[is_ic_formal, "core_cost_pnl"] = (
            0.5 * (core_net_unit.loc[is_ic_formal] - frame.loc[is_ic_formal, "core_future_unit_ret"])
        )
    frame["is_real"] = frame["date"] >= base.REAL_START[product]
    # EOD state can be zero on an exit day even though the old position earned (or
    # paid) that day's P&L/cost.  The max of adjacent booked states is the fair
    # unit-day denominator for an entry/exit-inclusive exposure comparison.
    state = pd.DataFrame(index=frame.index)
    state["core_futures"] = 0.5
    state["momentum"] = 0.5 * frame["momentum_weight"].astype(float)
    state["grid"] = frame["total_units"].astype(float) - state["core_futures"] - state["momentum"]
    if state["grid"].lt(-1e-12).any() or state["grid"].gt(1.0 + 1e-12).any():
        raise RuntimeError(f"{product}: invalid grid state")
    frame["core_exposure_units"] = state["core_futures"]
    frame["momentum_exposure_units"] = np.maximum(state["momentum"], state["momentum"].shift(1).fillna(0.0))
    frame["grid_exposure_units"] = np.maximum(state["grid"], state["grid"].shift(1).fillna(0.0))
    return frame


def daily_returns(frame: pd.DataFrame, mask: int) -> np.ndarray:
    active = {name: bool(mask & PLAYER_BIT[name]) for name in PLAYERS}
    n = len(frame)
    # Generic paths (IM formal and both current tails): futures costs remain a
    # multiplicative factor, exactly matching their official formula.
    pnl = np.zeros(n)
    factor = np.ones(n)
    for player, pnl_col, cost_col in (
        ("core_index_growth", "core_index_pnl", None),
        ("core_basis_excess", "core_basis_pnl", None),
        ("core_futures_cost", None, "core_cost"),
        ("momentum", "momentum_pnl", "momentum_cost"),
        ("grid", "grid_pnl", "grid_cost"),
        ("long_put", "put_pnl", "put_cost"),
        ("short_call", "call_pnl", "call_cost"),
    ):
        if active[player]:
            if pnl_col is not None:
                pnl += frame[pnl_col].to_numpy(dtype=float)
            if cost_col is not None:
                factor *= 1.0 - frame[cost_col].to_numpy(dtype=float)
    ret = (1.0 + pnl) * factor - 1.0
    if active["cash"]:
        ret += frame["cash_ret"].to_numpy(dtype=float)

    # The frozen IC quarter-T3 checkpoint applies futures costs inside its two
    # sleeves, then applies Put cost to the combined futures/Put result, and grid
    # net return afterwards.  Its fixed-core cost is therefore an additive player.
    special = frame["formula"].eq("ic_formal").to_numpy()
    if special.any():
        ic_pnl = np.zeros(n)
        for player, column in (
            ("core_index_growth", "core_index_pnl"),
            ("core_basis_excess", "core_basis_pnl"),
            ("core_futures_cost", "core_cost_pnl"),
            ("momentum", "momentum_pnl"),
            ("long_put", "put_pnl"),
        ):
            if active[player]:
                ic_pnl += frame[column].to_numpy(dtype=float)
        ic_ret = (1.0 + ic_pnl) * (1.0 - frame["put_cost"].to_numpy(dtype=float) if active["long_put"] else 1.0) - 1.0
        if active["grid"]:
            ic_ret += frame["grid_pnl"].to_numpy(dtype=float)
        if active["cash"]:
            ic_ret += frame["cash_ret"].to_numpy(dtype=float)
        ret[special] = ic_ret[special]
    return ret


def characteristic_values(frame: pd.DataFrame) -> dict[int, float]:
    values: dict[int, float] = {}
    for mask in range(1 << len(PLAYERS)):
        ret = daily_returns(frame, mask)
        if np.any(ret <= -1.0):
            raise RuntimeError(f"invalid counterfactual: {mask}")
        values[mask] = float(np.log1p(ret).sum())
    return values


def owen_values(values: dict[int, float]) -> dict[str, float]:
    """Hierarchical Shapley/Owen allocation: core children sum to fixed F."""
    result: dict[str, float] = {}
    group_bits = [sum(PLAYER_BIT[player] for player in group) for group in GROUPS]
    for player in PLAYERS:
        group_idx = next(i for i, group in enumerate(GROUPS) if player in group)
        own_group = GROUPS[group_idx]
        other_groups = [i for i in range(len(GROUPS)) if i != group_idx]
        total = 0.0
        for choice in range(1 << len(other_groups)):
            before = 0
            count = 0
            for j, group_index in enumerate(other_groups):
                if choice & (1 << j):
                    before |= group_bits[group_index]
                    count += 1
            outer_weight = FACTORIAL(count) * FACTORIAL(len(GROUPS) - count - 1) / FACTORIAL(len(GROUPS))
            inner_others = [name for name in own_group if name != player]
            for subset in range(1 << len(inner_others)):
                same_group_before = 0
                size = 0
                for j, name in enumerate(inner_others):
                    if subset & (1 << j):
                        same_group_before |= PLAYER_BIT[name]
                        size += 1
                inner_weight = FACTORIAL(size) * FACTORIAL(len(own_group) - size - 1) / FACTORIAL(len(own_group))
                total += outer_weight * inner_weight * (
                    values[before | same_group_before | PLAYER_BIT[player]]
                    - values[before | same_group_before]
                )
        result[player] = total
    return result


def old_component_attribution(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    # Reuse the validated six-player construction from the preceding attribution.
    return base.shapley_log(frame)


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {OUT}")
    OUT.mkdir(parents=True)
    all_daily: list[pd.DataFrame] = []
    normalized_rows: list[dict[str, object]] = []
    basis_rows: list[dict[str, object]] = []
    verification: dict[str, object] = {}

    for product in ("IC", "IM"):
        frame = build_product(product)
        reconstructed = daily_returns(frame, (1 << len(PLAYERS)) - 1)
        parity = np.abs(reconstructed - frame["baseline_ret"].to_numpy(dtype=float))
        if parity.max() > 2e-12:
            raise RuntimeError(f"{product}: fixed-core decomposition parity failed: {parity.max()}")
        frame["decomposition_ret"] = reconstructed
        frame["decomposition_abs_error"] = parity
        all_daily.append(frame)
        verification[product] = {}
        for window, sample in (
            ("real_only", frame.loc[frame["is_real"]].copy()),
            ("full_extended", frame.copy()),
        ):
            sample = sample.reset_index(drop=True)
            old, old_audit = old_component_attribution(sample)
            old_map = old.set_index("component")["log_contribution"].to_dict()
            values = characteristic_values(sample)
            owen = owen_values(values)
            full_log = values[(1 << len(PLAYERS)) - 1]
            core_child_sum = sum(owen[name] for name in CORE_PLAYERS)
            total_error = sum(owen.values()) - full_log
            parent_error = core_child_sum - old_map["core_futures"]
            if abs(total_error) > 2e-12 or abs(parent_error) > 2e-12:
                raise RuntimeError(f"{product}/{window}: Owen reconciliation failed {total_error}, {parent_error}")
            verification[product][window] = {
                "rows": int(len(sample)),
                "start": sample["date"].min().date().isoformat(),
                "end": sample["date"].max().date().isoformat(),
                "max_daily_decomposition_abs_error": float(sample["decomposition_abs_error"].max()),
                "total_log_return": full_log,
                "total_nav_multiple": float(math.exp(full_log)),
                "owen_total_log_error": total_error,
                "fixed_f_parent_reconciliation_error": parent_error,
            }
            years = len(sample) / 252.0
            for component, exposure_col in (
                ("core_futures", "core_exposure_units"),
                ("momentum", "momentum_exposure_units"),
                ("grid", "grid_exposure_units"),
            ):
                unit_days = float(sample[exposure_col].sum())
                unit_years = unit_days / 252.0
                contribution = float(old_map[component])
                if unit_years <= 0:
                    raise RuntimeError(f"{product}/{window}/{component}: no unit exposure")
                annual_log_per_unit = contribution / unit_years
                normalized_rows.append({
                    "product": product,
                    "window": window,
                    "component": component,
                    "rows": int(len(sample)),
                    "calendar_years": years,
                    "active_days": int(sample[exposure_col].gt(0).sum()),
                    "exposure_unit_days": unit_days,
                    "exposure_unit_years": unit_years,
                    "average_booked_units": unit_days / len(sample),
                    "log_contribution": contribution,
                    "calendar_annual_log_contribution": contribution / years,
                    "annual_log_contribution_per_1x_exposure": annual_log_per_unit,
                    "equivalent_annual_return_per_1x_exposure": math.expm1(annual_log_per_unit),
                    "margin_unit_years_at_30pct": 0.30 * unit_years,
                })
            for component, label in (
                ("core_index_growth", "指数价格增长"),
                ("core_basis_excess", "期货相对指数的贴水/基差超额"),
                ("core_futures_cost", "固定F开仓与展期成本"),
            ):
                contribution = float(owen[component])
                basis_rows.append({
                    "product": product,
                    "window": window,
                    "component": component,
                    "label": label,
                    "log_contribution": contribution,
                    "equivalent_compound_return": math.expm1(contribution),
                    "share_of_fixed_f_log_contribution": contribution / core_child_sum if abs(core_child_sum) > 1e-15 else np.nan,
                    "fixed_f_log_contribution": core_child_sum,
                    "fixed_f_equivalent_compound_return": math.expm1(core_child_sum),
                })

    daily = pd.concat(all_daily, ignore_index=True)
    daily.to_csv(OUT / "daily_fixed_core_decomposition.csv.gz", index=False, compression="gzip")
    normalized = pd.DataFrame(normalized_rows)
    normalized.to_csv(OUT / "normalized_futures_contributions.csv", index=False)
    basis = pd.DataFrame(basis_rows)
    basis.to_csv(OUT / "fixed_core_index_basis_contributions.csv", index=False)
    (OUT / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")

    cn = {"core_futures": "固定F", "momentum": "动量F", "grid": "网格"}
    lines = [
        "# v1.3-r7 单位暴露标准化与固定F指数/基差归因",
        "",
        "截至 2026-09-10；研究回放，不构成实盘授权或下单建议。",
        "",
        "## 口径",
        "",
        "- 单位暴露：固定F=`0.5`，动量F=`0.5 × momentum_execution_weight`，网格=`0/1`。对退出日使用相邻交易日的较大账面单位，以覆盖退出日仍发生的收益和成本。",
        "- 标准化数值为全路径 Shapley 对数贡献除以单位暴露年（252个单位日）；`equivalent_annual_return_per_1x_exposure`仅是对应对数贡献的复利映射，不是可相加或可独立交易的策略收益。每1倍期货均使用相同30%保证金缓冲，故改为保证金日只会等比例放大数值，不改变F三腿排序。",
        "- 固定F细分采用层级 Shapley/Owen 归因，因此“指数价格增长 + 期货相对指数的贴水/基差超额 + 固定F交易成本”严格加总回原固定F归因。基差项是期货相对价格指数的实际超额，包含贴水收敛、基差变动、收盘展期桥接和跟踪差，不等同于静态贴水率。",
        "",
    ]
    for product in ("IC", "IM"):
        for window in ("real_only", "full_extended"):
            part = normalized[(normalized["product"] == product) & (normalized["window"] == window)]
            lines += [f"## {product} / {window}：每1倍暴露年", "", "|腿|平均单位|单位年|年化对数贡献|等效年复合|", "|---|---:|---:|---:|---:|"]
            for _, row in part.iterrows():
                lines.append(f"|{cn[row['component']]}|{row['average_booked_units']:.3f}|{row['exposure_unit_years']:.3f}|{row['annual_log_contribution_per_1x_exposure']:.2%}|{row['equivalent_annual_return_per_1x_exposure']:.2%}|")
            child = basis[(basis["product"] == product) & (basis["window"] == window)]
            lines += ["", "固定F细分：", "", "|成分|对数贡献|等效复合|占固定F对数贡献|", "|---|---:|---:|---:|"]
            for _, row in child.iterrows():
                lines.append(f"|{row['label']}|{row['log_contribution']:.6f}|{row['equivalent_compound_return']:.2%}|{row['share_of_fixed_f_log_contribution']:.1%}|")
            lines.append("")
    lines += [
        "真实期：IC自2022-09-19，IM自2022-07-22。全历史自2015-04-16，含模型延伸；IM模型段使用平均基差延伸，不能与真实期等同。",
    ]
    (OUT / "record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()
