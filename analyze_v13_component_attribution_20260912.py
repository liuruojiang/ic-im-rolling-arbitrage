"""v1.3-r7 component-return attribution (research only; no trading action)."""
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "v13_component_attribution_20260912"
ONE_WAY = 0.0001
CASH_DAILY = 1.03 ** (1.0 / 252.0) - 1.0
COMPONENTS = ("core_futures", "momentum", "grid", "long_put", "short_call", "cash")
REAL_START = {"IC": pd.Timestamp("2022-09-19"), "IM": pd.Timestamp("2022-07-22")}


def read_csv(path: Path, **kwargs: object) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=["date"], low_memory=False, **kwargs)


def _standardize(df: pd.DataFrame, product: str, formula: str) -> pd.DataFrame:
    result = df.copy()
    result["product"] = product
    result["formula"] = formula
    for col in [
        "core_pnl", "momentum_pnl", "grid_pnl", "put_pnl", "call_pnl",
        "core_cost", "momentum_cost", "grid_cost", "put_cost", "call_cost", "cash_ret",
    ]:
        if col not in result:
            result[col] = 0.0
        result[col] = result[col].astype(float)
    return result


def exact_future_cost_split(frame: pd.DataFrame) -> None:
    """Allocate one total futures-cost factor across three sleeves exactly."""
    raw = frame[["core_cost", "momentum_cost", "grid_cost"]].to_numpy(dtype=float)
    total = raw.sum(axis=1)
    shares = np.zeros_like(raw)
    nonzero = total > 0.0
    shares[nonzero] = raw[nonzero] / total[nonzero, None]
    factors = np.ones_like(raw)
    factors[nonzero] = (1.0 - total[nonzero, None]) ** shares[nonzero]
    effective = 1.0 - factors
    frame["core_cost"] = effective[:, 0]
    frame["momentum_cost"] = effective[:, 1]
    frame["grid_cost"] = effective[:, 2]


def load_ic_formal() -> pd.DataFrame:
    formal = read_csv(ROOT / "quant_param_scan_runs" / "20260904_ic_v13_full_roll_tenor_timing_v2" / "candidate_checkpoints" / "quarter_T3_fixed.csv.gz")
    frame = formal.copy()
    w = frame["momentum_weight"].astype(float)
    unit_gross = frame["futures_gross_ret"].astype(float)
    # This is the exact quarter-T3 compose formula.  The starting core trade costs
    # one-way on the first row; later core rolls cost two-way.  The checkpoint's
    # stored momentum_cost_rate is already half of the full momentum-sleeve cost.
    core_cost_full = ONE_WAY * (frame.index.to_series().eq(frame.index[0]).astype(float)) + 2.0 * ONE_WAY * frame["roll_event"].astype(float)
    core_net = (1.0 + unit_gross) * (1.0 - core_cost_full) - 1.0
    momentum_net = (1.0 + w * unit_gross) * (1.0 - 2.0 * frame["momentum_cost_rate"].astype(float)) - 1.0
    frame["core_pnl"] = 0.5 * core_net
    frame["momentum_pnl"] = 0.5 * momentum_net
    frame["grid_pnl"] = frame["grid_net_increment"].astype(float)
    frame["put_pnl"] = frame["put_pnl_ret"].astype(float)
    frame["put_cost"] = frame["put_cost_rate"].astype(float)
    frame["cash_ret"] = frame["cash_weight"].astype(float) * CASH_DAILY
    frame["baseline_ret"] = frame["ret"].astype(float)
    frame["data_layer"] = np.where(frame["date"] < REAL_START["IC"], "model", "real")
    return _standardize(frame, "IC", "ic_formal")


def load_im_formal() -> pd.DataFrame:
    raw = read_csv(ROOT / "quant_param_scan_runs" / "20260823_im_grid160_put_carry_scan_v23" / "daily_outputs" / "daily_candidates.csv.gz")
    selected = raw[raw["variant"].eq("current_4tier_mom3")].copy()
    selected = pd.concat([
        selected[selected["scenario"].eq("model_avg_basis") & selected["date"].lt(REAL_START["IM"])],
        selected[selected["scenario"].eq("real_actual_basis") & selected["date"].ge(REAL_START["IM"])],
    ], ignore_index=True).sort_values("date")
    formal = read_csv(ROOT / "outputs" / "ic_im_mainline_v1_3_fixed_performance_v5" / "im_daily.csv.gz")
    frame = formal.merge(selected, on="date", validate="one_to_one", suffixes=("", "_src"))
    w = frame["momentum_weight"].astype(float)
    turnover = frame["momentum_turnover"].astype(float)
    base_gross = frame["base_gross_ret"].astype(float) + frame["base_basis_ret"].astype(float)
    overlay_gross = frame["overlay_gross_ret"].astype(float) + frame["overlay_basis_ret"].astype(float)
    frame["core_pnl"] = 0.5 * base_gross
    frame["momentum_pnl"] = 0.5 * w * base_gross
    frame["grid_pnl"] = overlay_gross
    frame["put_pnl"] = 0.5 * frame["put_pnl_ret"].astype(float)
    frame["call_pnl"] = 0.5 * frame["call_pnl_ret"].astype(float)
    frame["core_cost"] = 0.5 * frame["base_futures_cost_rate"].astype(float)
    frame["momentum_cost"] = 0.5 * (ONE_WAY * turnover + 2 * ONE_WAY * w * frame["roll_event"].astype(float))
    frame["grid_cost"] = frame["overlay_cost_rate"].astype(float)
    frame["put_cost"] = 0.5 * frame["put_cost_rate"].astype(float)
    frame["call_cost"] = 0.5 * frame["call_cost_rate"].astype(float)
    frame["cash_ret"] = frame["cash_weight"].astype(float) * CASH_DAILY
    exact_future_cost_split(frame)
    frame["baseline_ret"] = frame["ret"].astype(float)
    frame["data_layer"] = np.where(frame["date"] < REAL_START["IM"], "model", "real")
    return _standardize(frame, "IM", "generic")


def load_tail(product: str) -> pd.DataFrame:
    tail = read_csv(ROOT / "outputs" / "nav_r7_complete_refresh_20260911" / f"{product.lower()}_tail_daily.csv")
    w = tail["momentum_weight"].astype(float)
    units = tail["total_units"].astype(float)
    grid = units - 0.5 - 0.5 * w
    gross_per_unit = tail["futures_gross_ret"].astype(float) / units
    roll = tail["roll_event"].astype(float)
    turnover = tail["momentum_turnover"].astype(float)
    tail["core_pnl"] = 0.5 * gross_per_unit
    tail["momentum_pnl"] = 0.5 * w * gross_per_unit
    tail["grid_pnl"] = grid * gross_per_unit
    tail["put_pnl"] = tail["put_pnl_ret"].astype(float)
    tail["core_cost"] = ONE_WAY * roll
    tail["momentum_cost"] = 0.5 * (ONE_WAY * turnover + 2 * ONE_WAY * w * roll)
    tail["grid_cost"] = 0.0
    tail["put_cost"] = tail["put_cost_rate"].astype(float)
    if product == "IM":
        tail["call_pnl"] = tail["call_pnl_ret"].astype(float)
        tail["call_cost"] = tail["call_cost_rate"].astype(float)
    tail["cash_ret"] = tail["cash_weight"].astype(float) * CASH_DAILY
    exact_future_cost_split(tail)
    tail["baseline_ret"] = tail["ret"].astype(float)
    return _standardize(tail, product, "generic")


def daily_returns(frame: pd.DataFrame, mask: int) -> np.ndarray:
    active = {name: bool(mask & (1 << i)) for i, name in enumerate(COMPONENTS)}
    pnl = np.zeros(len(frame))
    cost_factor = np.ones(len(frame))
    pairs = (("core_futures", "core"), ("momentum", "momentum"), ("grid", "grid"), ("long_put", "put"), ("short_call", "call"))
    for component, prefix in pairs:
        if active[component]:
            pnl += frame[f"{prefix}_pnl"].to_numpy()
            cost_factor *= 1.0 - frame[f"{prefix}_cost"].to_numpy()
    ret = (1.0 + pnl) * cost_factor - 1.0
    if active["cash"]:
        ret += frame["cash_ret"].to_numpy()
    ic_formal = frame["formula"].eq("ic_formal").to_numpy()
    if ic_formal.any():
        ic_pnl = np.zeros(len(frame))
        if active["core_futures"]:
            ic_pnl += frame["core_pnl"].to_numpy()
        if active["momentum"]:
            ic_pnl += frame["momentum_pnl"].to_numpy()
        if active["long_put"]:
            ic_pnl += frame["put_pnl"].to_numpy()
        ic_ret = (1.0 + ic_pnl) * (1.0 - frame["put_cost"].to_numpy() if active["long_put"] else 1.0) - 1.0
        if active["grid"]:
            ic_ret += frame["grid_pnl"].to_numpy()
        if active["cash"]:
            ic_ret += frame["cash_ret"].to_numpy()
        ret[ic_formal] = ic_ret[ic_formal]
    return ret


def shapley_log(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    n = len(COMPONENTS)
    values: dict[int, float] = {}
    for mask in range(1 << n):
        ret = daily_returns(frame, mask)
        if np.any(ret <= -1.0):
            raise RuntimeError(f"counterfactual return <= -100% for mask {mask}")
        values[mask] = float(np.log1p(ret).sum())
    factorial = math.factorial
    phi: dict[str, float] = {}
    for i, component in enumerate(COMPONENTS):
        total = 0.0
        bit = 1 << i
        for mask in range(1 << n):
            if mask & bit:
                continue
            size = mask.bit_count()
            weight = factorial(size) * factorial(n - size - 1) / factorial(n)
            total += weight * (values[mask | bit] - values[mask])
        phi[component] = total
    all_log = values[(1 << n) - 1]
    return pd.DataFrame([{
        "component": component,
        "log_contribution": phi[component],
        "equivalent_compound_return": math.expm1(phi[component]),
        "share_of_total_log_return": phi[component] / all_log if abs(all_log) > 1e-15 else np.nan,
    } for component in COMPONENTS]), {"total_log_return": all_log, "nav_multiple": math.exp(all_log), "shapley_sum_error": sum(phi.values()) - all_log}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    all_frames: list[pd.DataFrame] = []
    for product, formal_loader in (("IC", load_ic_formal), ("IM", load_im_formal)):
        formal = formal_loader()
        tail = load_tail(product)
        merged = pd.concat([formal, tail], ignore_index=True).sort_values("date").reset_index(drop=True)
        if merged["date"].duplicated().any():
            raise RuntimeError(f"{product} duplicate dates")
        # Exact row-level reconstruction at the formal/current boundary.
        reconstructed = daily_returns(merged, (1 << len(COMPONENTS)) - 1)
        err = np.abs(reconstructed - merged["baseline_ret"].to_numpy())
        if err.max() > 2e-12:
            bad = merged.loc[err > 2e-12, ["date", "baseline_ret"]].head(3)
            raise RuntimeError(f"{product} component reconstruction failed: {err.max()}, {bad.to_dict('records')}")
        merged["reconstructed_ret"] = reconstructed
        merged["reconstruction_abs_error"] = err
        merged["is_real"] = merged["date"] >= REAL_START[product]
        all_frames.append(merged)

    daily = pd.concat(all_frames, ignore_index=True)
    daily.to_csv(OUT / "daily_component_inputs.csv.gz", index=False, compression="gzip")
    rows: list[dict[str, object]] = []
    verification: dict[str, object] = {}
    for product, frame in daily.groupby("product", sort=False):
        verification[str(product)] = {}
        for window, subset in (("full_extended", frame), ("real_only", frame[frame["is_real"]])):
            shapley, audit = shapley_log(subset.reset_index(drop=True))
            total_nav = float(np.prod(1.0 + subset["baseline_ret"].to_numpy()))
            audit["rows"] = int(len(subset))
            audit["start"] = subset["date"].min().date().isoformat()
            audit["end"] = subset["date"].max().date().isoformat()
            audit["direct_nav_multiple"] = total_nav
            audit["nav_reconstruction_error"] = total_nav - audit["nav_multiple"]
            audit["max_daily_reconstruction_abs_error"] = float(subset["reconstruction_abs_error"].max())
            verification[str(product)][window] = audit
            shapley.insert(0, "window", window)
            shapley.insert(0, "product", product)
            rows.extend(shapley.to_dict("records"))
    contributions = pd.DataFrame(rows)
    contributions.to_csv(OUT / "component_contributions.csv", index=False)
    (OUT / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    labels = {"core_futures": "F固定持有", "momentum": "F动量", "grid": "网格", "long_put": "买Put", "short_call": "卖Call", "cash": "现金利息"}
    lines = ["# v1.3-r7 组件收益归因", "", "截至 2026-09-10 的研究回放；不构成实盘授权或下单建议。", "", "采用全路径 Shapley 对数收益归因：每个组件的直接损益、直接交易成本及乘法交互被公平分配，六项对数贡献严格加总为策略总对数收益。`equivalent_compound_return` 是将单项对数贡献单独映射为复合收益，不能与其他项直接相加。现金项使用实际策略已占用保证金/权利金后的现金收益，因此未把资金占用反向分摊给期权或期货腿。", ""]
    show = contributions.copy()
    show["component"] = show["component"].map(labels)
    for product in ("IC", "IM"):
        for window in ("real_only", "full_extended"):
            part = show[(show["product"] == product) & (show["window"] == window)]
            lines += [f"## {product} / {window}", "", "|组件|对数贡献|等效复合收益|", "|---|---:|---:|"]
            for _, row in part.iterrows():
                lines.append(f"|{row['component']}|{row['log_contribution']:.6f}|{row['equivalent_compound_return']:.2%}|")
            lines.append("")
    lines += ["真实期：IC 自 2022-09-19；IM 自 2022-07-22。全历史延伸自 2015-04-16；其中上市前/真实期前的模型段（尤其 IM 的平均基差延伸）仅为模型参考，不能与真实交易结果等同。IC 主线没有卖 Call，因此该项为零。"]
    (OUT / "record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()
