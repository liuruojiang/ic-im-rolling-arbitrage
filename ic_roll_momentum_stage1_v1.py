from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "ic_roll_momentum_stage1_v1"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
IC_DAILY = ROOT / "outputs" / "ic_monthly_discount_roll_v1" / "daily_nav.csv"
IC_MANIFEST = ROOT / "outputs" / "ic_monthly_discount_roll_v1" / "data_manifest.json"
FROZEN_IC = ROOT / "outputs" / "ic_put_grid_call_combined_v2" / "daily_candidates.csv.gz"
SIGNAL_DIR = Path(
    r"D:\动量策略\A 股股指多头策略\quant_param_scan_runs"
    r"\20260822_a_500_ma110_mom24_w2_abs20_off_0_50_abs20_off_gt0_50_50_blend"
)
SIGNAL_DAILY = SIGNAL_DIR / "daily_curves.csv"
SIGNAL_META = SIGNAL_DIR / "scan_meta.json"
OUTPUT = ROOT / "outputs" / VERSION
STAGING = ROOT / "outputs" / f".{VERSION}.staging"

SPEC_SHA256 = "913fc9b33ac55166f75ec73cad970b9e0e1ddeec49a918b5f22bbcf4b6e380f2"
ONE_WAY_COST = 0.0001
MARGIN_RATE = 0.30
CASH_ANNUAL = 0.03
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252.0) - 1.0

FROZEN_HASHES = {
    SPEC: SPEC_SHA256,
    IC_DAILY: "bd575ee101b77791bfad3968e0cd221fb189624b8439d9e5dcecddcd944c092d",
    IC_MANIFEST: "b36e29959aeedd4173dd71beae076160efd0571c508dc05d8d68a02c35d0f6b3",
    FROZEN_IC: "15e38d5754f25bddf829b5fec1b8692c1d6a55a4af902385740f5f507ead15b2",
    SIGNAL_DAILY: "e183c215c7bea2d0591e789ac7c457f5fc6b9860e5d3630270055bcf4bca1dbf",
    SIGNAL_META: "a0e53f7e5c4df6d5a45e1ae837072c44f8b83a21f702dd38460e31796d3d54a4",
}

WINDOWS = (
    ("full", None),
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("3y", pd.DateOffset(years=3)),
    ("1y", pd.DateOffset(years=1)),
)

STRATEGIES = {
    "bare_roll_ic": "裸滚IC",
    "momentum_gated_ic": "动量门控IC",
    "roll50_momentum50_ic": "裸滚50% + 动量50%",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_inputs() -> dict[str, str]:
    if OUTPUT.exists() or STAGING.exists():
        raise FileExistsError(f"Formal or staging output already exists: {OUTPUT}")
    if not SPEC_HASH.exists() or SPEC_HASH.read_text(encoding="utf-8").split()[0].lower() != SPEC_SHA256:
        raise RuntimeError("Specification hash sidecar mismatch")
    hashes: dict[str, str] = {}
    for path, expected in FROZEN_HASHES.items():
        if not path.exists():
            raise FileNotFoundError(path)
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"Frozen input changed: {path}: {actual} != {expected}")
        try:
            label = str(path.relative_to(ROOT))
        except ValueError:
            label = str(path)
        hashes[label] = actual
    return hashes


def load_signal() -> pd.DataFrame:
    raw = pd.read_csv(SIGNAL_DAILY, header=[0, 1], index_col=0, parse_dates=True)
    signal = raw["blend50"].reset_index()
    signal.columns = ["date", *signal.columns[1:]]
    signal["date"] = pd.to_datetime(signal["date"])
    signal = signal[
        ["date", "close", "score", "abs20", "desired_weight", "weight"]
    ].rename(
        columns={
            "close": "csi500_signal_close",
            "weight": "momentum_weight",
        }
    )
    if signal["date"].duplicated().any():
        raise RuntimeError("Duplicate signal dates")
    return signal.sort_values("date").reset_index(drop=True)


def build_daily() -> tuple[pd.DataFrame, dict[str, Any]]:
    ic = pd.read_csv(IC_DAILY, parse_dates=["date"], low_memory=False).sort_values("date")
    signal = load_signal()
    frame = ic.merge(signal, on="date", how="left", validate="one_to_one").reset_index(drop=True)
    required = ["score", "abs20", "desired_weight", "momentum_weight"]
    if frame[required].isna().any().any():
        raise RuntimeError(f"Missing signal on IC dates: {frame[required].isna().sum().to_dict()}")
    allowed_weights = {0.0, 0.5, 1.0}
    observed = set(frame["momentum_weight"].astype(float).unique())
    if not observed.issubset(allowed_weights):
        raise RuntimeError(f"Unexpected momentum weights: {sorted(observed)}")
    lag_error = float(
        (
            frame["momentum_weight"].iloc[1:].to_numpy(dtype=float)
            - frame["desired_weight"].shift(1).iloc[1:].to_numpy(dtype=float)
        ).max()
    )
    lag_abs_error = float(
        np.abs(
            frame["momentum_weight"].iloc[1:].to_numpy(dtype=float)
            - frame["desired_weight"].shift(1).iloc[1:].to_numpy(dtype=float)
        ).max()
    )
    if lag_abs_error > 1e-14:
        raise RuntimeError(f"Signal timing parity failed: {lag_error}, {lag_abs_error}")

    frame["roll_event"] = frame["roll_to"].fillna("").astype(str).ne("")
    frame["bare_roll_ic_units"] = 1.0
    frame["bare_roll_ic_futures_cost_rate"] = frame["cost_rate"].astype(float)
    frame["bare_roll_ic_futures_gross_ret"] = frame["ic_gross_ret"].astype(float)
    frame["bare_roll_ic_futures_net_ret"] = (
        (1.0 + frame["bare_roll_ic_futures_gross_ret"])
        * (1.0 - frame["bare_roll_ic_futures_cost_rate"])
        - 1.0
    )
    frame["bare_roll_ic_cash_weight"] = 1.0 - MARGIN_RATE
    frame["bare_roll_ic_ret"] = (
        frame["bare_roll_ic_futures_net_ret"]
        + frame["bare_roll_ic_cash_weight"] * CASH_DAILY
    )

    frame["momentum_gated_ic_units"] = frame["momentum_weight"].astype(float)
    frame["momentum_turnover"] = frame["momentum_gated_ic_units"].diff().abs()
    frame.loc[0, "momentum_turnover"] = abs(float(frame.loc[0, "momentum_gated_ic_units"]))
    frame["momentum_trade_cost_rate"] = ONE_WAY_COST * frame["momentum_turnover"]
    frame["momentum_roll_cost_rate"] = (
        2.0 * ONE_WAY_COST
        * frame["momentum_gated_ic_units"]
        * frame["roll_event"].astype(float)
    )
    frame["momentum_gated_ic_futures_cost_rate"] = (
        frame["momentum_trade_cost_rate"] + frame["momentum_roll_cost_rate"]
    )
    frame["momentum_gated_ic_futures_gross_ret"] = (
        frame["momentum_gated_ic_units"] * frame["ic_gross_ret"]
    )
    frame["momentum_gated_ic_futures_net_ret"] = (
        (1.0 + frame["momentum_gated_ic_futures_gross_ret"])
        * (1.0 - frame["momentum_gated_ic_futures_cost_rate"])
        - 1.0
    )
    frame["momentum_gated_ic_cash_weight"] = (
        1.0 - MARGIN_RATE * frame["momentum_gated_ic_units"]
    )
    frame["momentum_gated_ic_ret"] = (
        frame["momentum_gated_ic_futures_net_ret"]
        + frame["momentum_gated_ic_cash_weight"] * CASH_DAILY
    )

    frame["roll50_momentum50_ic_units"] = (
        0.5 + 0.5 * frame["momentum_gated_ic_units"]
    )
    frame["roll50_momentum50_ic_futures_cost_rate"] = (
        0.5 * frame["bare_roll_ic_futures_cost_rate"]
        + 0.5 * frame["momentum_gated_ic_futures_cost_rate"]
    )
    frame["roll50_momentum50_ic_futures_gross_ret"] = (
        frame["roll50_momentum50_ic_units"] * frame["ic_gross_ret"]
    )
    frame["roll50_momentum50_ic_cash_weight"] = (
        0.5 * frame["bare_roll_ic_cash_weight"]
        + 0.5 * frame["momentum_gated_ic_cash_weight"]
    )
    frame["roll50_momentum50_ic_ret"] = (
        0.5 * frame["bare_roll_ic_ret"] + 0.5 * frame["momentum_gated_ic_ret"]
    )

    frozen = pd.read_csv(FROZEN_IC, parse_dates=["date"], low_memory=False)
    frozen = frozen[
        frozen["layer"].eq("model") & frozen["candidate"].eq("model_core_ic_no_put")
    ][["date", "cash_ret"]].sort_values("date")
    parity = frame[["date", "bare_roll_ic_ret"]].merge(
        frozen, on="date", validate="one_to_one"
    )
    bare_parity = float((parity["bare_roll_ic_ret"] - parity["cash_ret"]).abs().max())
    if len(parity) != len(frame) or bare_parity > 1e-12:
        raise RuntimeError(f"Frozen bare IC parity failed: rows={len(parity)}, error={bare_parity}")

    flat = frame["momentum_gated_ic_units"].eq(0.0)
    flat_gross = float(frame.loc[flat, "momentum_gated_ic_futures_gross_ret"].abs().max())
    flat_roll = float(frame.loc[flat, "momentum_roll_cost_rate"].abs().max())
    flat_cash = float((frame.loc[flat, "momentum_gated_ic_cash_weight"] - 1.0).abs().max())
    unit_values = sorted(float(value) for value in frame["roll50_momentum50_ic_units"].unique())
    cash_identity = float(
        (
            frame["roll50_momentum50_ic_cash_weight"]
            - (1.0 - MARGIN_RATE * frame["roll50_momentum50_ic_units"])
        ).abs().max()
    )
    if max(flat_gross, flat_roll, flat_cash, cash_identity) > 1e-14:
        raise RuntimeError("Flat-state or capital identity failed")
    if unit_values != [0.5, 0.75, 1.0]:
        raise RuntimeError(f"Unexpected blend IC units: {unit_values}")

    for strategy in STRATEGIES:
        ret = frame[f"{strategy}_ret"]
        if ret.isna().any() or ret.le(-1.0).any():
            raise RuntimeError(f"Invalid return path: {strategy}")
        frame[f"{strategy}_nav"] = (1.0 + ret).cumprod()
        frame[f"{strategy}_drawdown"] = (
            frame[f"{strategy}_nav"] / frame[f"{strategy}_nav"].cummax() - 1.0
        )

    audit = {
        "rows": int(len(frame)),
        "start": frame["date"].min().date().isoformat(),
        "end": frame["date"].max().date().isoformat(),
        "bare_frozen_parity_max_abs": bare_parity,
        "signal_t_plus_1_alignment_max_abs": lag_abs_error,
        "momentum_weight_values": sorted(float(value) for value in observed),
        "blend_unit_values": unit_values,
        "flat_days": int(flat.sum()),
        "half_momentum_days": int(frame["momentum_gated_ic_units"].eq(0.5).sum()),
        "full_momentum_days": int(frame["momentum_gated_ic_units"].eq(1.0).sum()),
        "momentum_change_days": int(frame["momentum_turnover"].gt(0).sum()),
        "roll_events": int(frame["roll_event"].sum()),
        "roll_events_while_momentum_held": int(
            (frame["roll_event"] & frame["momentum_gated_ic_units"].gt(0)).sum()
        ),
        "flat_gross_max_abs": flat_gross,
        "flat_roll_cost_max_abs": flat_roll,
        "flat_cash_error_max_abs": flat_cash,
        "blend_cash_identity_max_abs": cash_identity,
    }
    return frame, audit


def metric_values(sample: pd.DataFrame, strategy: str) -> dict[str, float]:
    returns = sample[f"{strategy}_ret"].astype(float)
    nav = (1.0 + returns).cumprod()
    ann_return = float(nav.iloc[-1] ** (252.0 / len(sample)) - 1.0)
    ann_vol = float(returns.std(ddof=0) * math.sqrt(252.0))
    drawdown = nav / nav.cummax() - 1.0
    max_dd = float(drawdown.min())
    return {
        "rows": int(len(sample)),
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "max_dd": max_dd,
        "calmar": ann_return / abs(max_dd) if max_dd < -1e-12 else np.nan,
        "sharpe_repo": ann_return / ann_vol if ann_vol > 1e-12 else np.nan,
        "final_nav": float(nav.iloc[-1]),
        "avg_ic_units": float(sample[f"{strategy}_units"].mean()),
        "avg_cash_weight": float(sample[f"{strategy}_cash_weight"].mean()),
        "futures_cost_total": float(sample[f"{strategy}_futures_cost_rate"].sum()),
    }


def build_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    end = frame["date"].max()
    rows: list[dict[str, Any]] = []
    for strategy in STRATEGIES:
        for window, offset in WINDOWS:
            requested = None if offset is None else end - offset
            sample = frame if requested is None else frame[frame["date"].ge(requested)]
            available = bool(requested is None or sample["date"].min() <= requested + pd.Timedelta(days=7))
            values = metric_values(sample, strategy) if available else {
                key: np.nan for key in (
                    "rows", "ann_return", "ann_vol", "max_dd", "calmar",
                    "sharpe_repo", "final_nav", "avg_ic_units", "avg_cash_weight",
                    "futures_cost_total",
                )
            }
            rows.append(
                {
                    "strategy": strategy,
                    "window": window,
                    "available": available,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    **values,
                }
            )
    return pd.DataFrame(rows)


def build_annual(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for year, sample in frame.groupby(frame["date"].dt.year):
        for strategy in STRATEGIES:
            rows.append(
                {
                    "year": int(year),
                    "strategy": strategy,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    **metric_values(sample, strategy),
                }
            )
    return pd.DataFrame(rows)


def build_events(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[
        frame["momentum_turnover"].gt(0) | frame["roll_event"],
        [
            "date", "contract", "momentum_gated_ic_units", "momentum_turnover",
            "roll_event", "momentum_trade_cost_rate", "momentum_roll_cost_rate",
            "roll_from", "roll_to",
        ],
    ].copy()


def pct(value: Any) -> str:
    return "N/A" if pd.isna(value) else f"{100.0 * float(value):.2f}%"


def write_record(frame: pd.DataFrame, metrics: pd.DataFrame, audit: dict[str, Any]) -> None:
    table = ["|路径|全周期|近10年|近5年|近3年|近1年|", "|---|---:|---:|---:|---:|---:|"]
    for strategy, label in STRATEGIES.items():
        block = metrics[metrics["strategy"].eq(strategy)].set_index("window")
        cells = [
            f"{pct(block.loc[name, 'ann_return'])} / {pct(block.loc[name, 'max_dd'])}"
            for name, _ in WINDOWS
        ]
        table.append(f"|{label}|{'|'.join(cells)}|")

    text = f"""# IC 分层研究第一层：裸滚 / 动量门控 / 50:50 v1

状态：研究完成；未批准实盘  
共同样本：{audit['start']} 至 {audit['end']}

## 结果

每格为年化收益 / 最大回撤。本层不含Put、Call或网格。

{chr(10).join(table)}

## 规则

- 动量：中证500 `MA110 / Mom24 / W2`，50% Abs20 OFF + 50% `Abs20 > 0`。
- 动量目标只为0/0.5/1；空仓时IC损益与换月成本均为0，资金全部为现金。
- 50:50为资本分袖：固定裸滚50%，动量门控50%；总IC名义只为0.5/0.75/1倍。
- 单边成本1bp；持仓换月双边2bp；每1倍IC使用30%保证金/缓冲，剩余现金按净年化3%计息。

## 审计

- 裸滚逐日复现冻结无Put/无网格IC路径，最大误差 {audit['bare_frozen_parity_max_abs']:.3e}。
- 动量权重与上一交易日目标错位误差 {audit['signal_t_plus_1_alignment_max_abs']:.3e}。
- 动量空仓/半仓/满仓日：{audit['flat_days']} / {audit['half_momentum_days']} / {audit['full_momentum_days']}；仓位变化日 {audit['momentum_change_days']}。
- IC换月事件 {audit['roll_events']} 次，其中动量持仓期间 {audit['roll_events_while_momentum_held']} 次。
- IC数据来自中金所官方结算价；中证500信号来自冻结价格指数快照。2015-04-16以前不做IC贴水外推。

## 边界

最终结算价不等于可保证成交价；本层未计盘口冲击、动态保证金或税费。结果仅是下一层研究底座，不修改V2主线或实盘配置。
"""
    (STAGING / "record.md").write_text(text, encoding="utf-8")


def git_value(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, check=False, text=True, capture_output=True)
    return result.stdout.strip()


def main() -> None:
    input_hashes = verify_inputs()
    STAGING.mkdir(parents=True)
    try:
        daily, audit = build_daily()
        metrics = build_metrics(daily)
        annual = build_annual(daily)
        events = build_events(daily)

        keep = [
            "date", "contract", "settle", "ic_gross_ret", "roll_event", "roll_from", "roll_to",
            "csi500_signal_close", "score", "abs20", "desired_weight", "momentum_weight",
            "momentum_turnover", "momentum_trade_cost_rate", "momentum_roll_cost_rate",
        ]
        for strategy in STRATEGIES:
            keep.extend(
                [
                    f"{strategy}_units", f"{strategy}_futures_gross_ret",
                    f"{strategy}_futures_cost_rate", f"{strategy}_cash_weight",
                    f"{strategy}_ret", f"{strategy}_nav", f"{strategy}_drawdown",
                ]
            )
        daily[keep].to_csv(STAGING / "daily_nav.csv.gz", index=False, compression="gzip")
        metrics.to_csv(STAGING / "metrics_by_window.csv", index=False)
        annual.to_csv(STAGING / "annual_metrics.csv", index=False)
        events.to_csv(STAGING / "events_and_rolls.csv", index=False)

        validation = {
            "version": VERSION,
            "created_at": datetime.now().astimezone().isoformat(),
            "input_hashes": input_hashes,
            "audit": audit,
            "all_checks_passed": bool(
                audit["bare_frozen_parity_max_abs"] <= 1e-12
                and audit["signal_t_plus_1_alignment_max_abs"] <= 1e-14
                and audit["flat_gross_max_abs"] <= 1e-14
                and audit["flat_roll_cost_max_abs"] <= 1e-14
                and audit["flat_cash_error_max_abs"] <= 1e-14
                and audit["blend_cash_identity_max_abs"] <= 1e-14
            ),
        }
        if not validation["all_checks_passed"]:
            raise RuntimeError(f"Formal validation failed: {validation}")
        (STAGING / "validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_record(daily, metrics, audit)

        files: dict[str, dict[str, Any]] = {}
        for path in sorted(STAGING.iterdir()):
            if path.name != "run_manifest.json":
                files[path.name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
        manifest = {
            "version": VERSION,
            "status": "research_only_not_live_approved",
            "created_at": datetime.now().astimezone().isoformat(),
            "git_commit": git_value("rev-parse", "HEAD"),
            "git_dirty": bool(git_value("status", "--porcelain")),
            "spec_sha256": SPEC_SHA256,
            "sample": {"start": audit["start"], "end": audit["end"], "rows": audit["rows"]},
            "inputs": input_hashes,
            "cost_and_capital": {
                "one_way_futures_cost": ONE_WAY_COST,
                "margin_buffer_per_1x": MARGIN_RATE,
                "cash_assumed_net_annual_return": CASH_ANNUAL,
                "put": "excluded", "call": "excluded", "grid": "excluded",
            },
            "files": files,
        }
        (STAGING / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        STAGING.rename(OUTPUT)
        print(metrics.to_string(index=False))
        print(f"Formal output: {OUTPUT}")
    except Exception:
        if STAGING.exists():
            shutil.rmtree(STAGING)
        raise


if __name__ == "__main__":
    main()
