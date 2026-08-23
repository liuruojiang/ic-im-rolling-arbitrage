from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
VERSION = "im_roll50_momentum50_fullcycle_put_v3"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
V2_DAILY = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_put_v2" / "daily_nav.csv.gz"
V2_MANIFEST = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_put_v2" / "data_manifest.json"
OUTPUT = ROOT / "outputs" / VERSION
PINNED = {
    V2_DAILY: "670e21d6e8350b64aea9e729a9ca49ea30c19ab901e2d771358e3f67dc84b4a4",
    V2_MANIFEST: "1f99cd9f07155b4659ecb65f4b229825e6264cc7a35eb58f20802b35ac766ab1",
}
CASH_DAILY = (1.0 + 0.03) ** (1.0 / 252.0) - 1.0
WINDOWS = (
    ("full", None),
    ("10y", pd.DateOffset(years=10)),
    ("5y", pd.DateOffset(years=5)),
    ("3y", pd.DateOffset(years=3)),
    ("1y", pd.DateOffset(years=1)),
)
STRATEGIES = {
    "no_put": "no_put_ret",
    "sleeve_matched_dynamic_put": "sleeve_matched_dynamic_put_ret",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_inputs() -> dict[str, str]:
    for path in (SPEC, SPEC_HASH, *PINNED):
        if not path.exists():
            raise FileNotFoundError(path)
    spec_expected = SPEC_HASH.read_text(encoding="utf-8").split()[0].lower()
    spec_actual = sha256(SPEC)
    if spec_actual != spec_expected:
        raise RuntimeError(f"Specification hash mismatch: {spec_actual} != {spec_expected}")
    actual = {path: sha256(path) for path in PINNED}
    for path, expected in PINNED.items():
        if actual[path] != expected:
            raise RuntimeError(f"Pinned input hash mismatch: {path}")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output already exists: {OUTPUT}")
    return {
        str(SPEC.relative_to(ROOT)): spec_actual,
        **{str(path.relative_to(ROOT)): value for path, value in actual.items()},
    }


def add_component(frame: pd.DataFrame, sleeve: str, scale: pd.Series | float) -> None:
    scale_series = pd.Series(scale, index=frame.index, dtype=float)
    frame[f"{sleeve}_put_scale"] = scale_series
    frame[f"{sleeve}_put_qty"] = frame["v2_target_put_qty"] * scale_series
    frame[f"{sleeve}_put_notional_fraction"] = frame["put_fraction"] * scale_series
    frame[f"{sleeve}_put_pnl_ret"] = frame["put_pnl_ret"] * scale_series
    frame[f"{sleeve}_put_cost_rate"] = frame["put_cost_rate"] * scale_series
    frame[f"{sleeve}_put_mark_fraction"] = frame["put_mark_fraction"] * scale_series


def build_daily() -> tuple[pd.DataFrame, dict[str, object]]:
    frame = pd.read_csv(V2_DAILY, parse_dates=["date"], low_memory=False)
    frame["v2_target_put_qty"] = 2.0 * frame["put_fraction"]
    if not set(frame["v2_target_put_qty"].round(12).unique()).issubset(
        {0.0, 1.0, 2.0, 3.0, 4.0}
    ):
        raise RuntimeError("Frozen V2 target Put quantity outside 0..4")

    add_component(frame, "bare_sleeve", 0.5)
    add_component(frame, "momentum_sleeve", 0.5 * frame["momentum_weight"])
    add_component(frame, "combined", frame["total_im_units"])

    identities: dict[str, float] = {}
    for field in (
        "put_scale", "put_qty", "put_notional_fraction", "put_pnl_ret",
        "put_cost_rate", "put_mark_fraction",
    ):
        expected = frame[f"bare_sleeve_{field}"] + frame[f"momentum_sleeve_{field}"]
        error = float((frame[f"combined_{field}"] - expected).abs().max())
        identities[f"sleeve_sum_{field}_max_abs"] = error
        if error > 1e-14:
            raise RuntimeError(f"Sleeve sum identity failed for {field}: {error}")

    unit_identity = float(
        (
            frame["total_im_units"]
            - (0.5 + 0.5 * frame["momentum_weight"])
        ).abs().max()
    )
    identities["total_im_units_formula_max_abs"] = unit_identity
    if unit_identity > 1e-14:
        raise RuntimeError(f"Total IM units identity failed: {unit_identity}")

    frame["sleeve_matched_dynamic_put_pre_cash_ret"] = (
        (1.0 + frame["baseline_pre_cash_ret"] + frame["combined_put_pnl_ret"])
        * (1.0 - frame["combined_put_cost_rate"])
        - 1.0
    )
    frame["sleeve_matched_dynamic_put_cash_weight_raw"] = (
        frame["blend_cash_weight"] - frame["combined_put_mark_fraction"]
    )
    if frame["sleeve_matched_dynamic_put_cash_weight_raw"].lt(-1e-12).any():
        raise RuntimeError("Sleeve-matched Put exceeds available cash")
    frame["sleeve_matched_dynamic_put_cash_weight"] = frame[
        "sleeve_matched_dynamic_put_cash_weight_raw"
    ].clip(lower=0.0)
    frame["sleeve_matched_dynamic_put_ret"] = (
        frame["sleeve_matched_dynamic_put_pre_cash_ret"]
        + frame["sleeve_matched_dynamic_put_cash_weight"] * CASH_DAILY
    )

    parity = float(
        (
            frame["sleeve_matched_dynamic_put_ret"]
            - frame["put_scaled_total_exposure_ret"]
        ).abs().max()
    )
    if parity > 1e-14:
        raise RuntimeError(f"Prior total-exposure path parity failed: {parity}")
    identities["prior_total_exposure_path_parity_max_abs"] = parity

    if frame["bare_sleeve_put_qty"].max() > 2.0 + 1e-12:
        raise RuntimeError("Bare sleeve Put exceeds 2 normalized contracts")
    if frame["momentum_sleeve_put_qty"].max() > 2.0 + 1e-12:
        raise RuntimeError("Momentum sleeve Put exceeds 2 normalized contracts")
    if frame["combined_put_qty"].max() > 4.0 + 1e-12:
        raise RuntimeError("Combined Put exceeds original V2 four-contract cap")

    for strategy, column in STRATEGIES.items():
        frame[f"{strategy}_nav"] = (1.0 + frame[column]).cumprod()
        frame[f"{strategy}_drawdown"] = (
            frame[f"{strategy}_nav"] / frame[f"{strategy}_nav"].cummax() - 1.0
        )
        if frame[column].isna().any() or frame[column].le(-1.0).any():
            raise RuntimeError(f"Invalid return path: {strategy}")

    audit = {
        "start": frame["date"].min().date().isoformat(),
        "end": frame["date"].max().date().isoformat(),
        "rows": int(len(frame)),
        "proxy_rows": int(frame["put_source"].eq("theoretical_csi1000_put").sum()),
        "real_rows": int(frame["put_source"].eq("real_mo_frozen_v2").sum()),
        "momentum_weight_values": sorted(float(value) for value in frame["momentum_weight"].unique()),
        "total_im_unit_values": sorted(float(value) for value in frame["total_im_units"].unique()),
        "max_v2_target_put_qty": float(frame["v2_target_put_qty"].max()),
        "max_bare_sleeve_put_qty": float(frame["bare_sleeve_put_qty"].max()),
        "max_momentum_sleeve_put_qty": float(frame["momentum_sleeve_put_qty"].max()),
        "max_combined_put_qty": float(frame["combined_put_qty"].max()),
        "max_combined_put_notional_fraction": float(frame["combined_put_notional_fraction"].max()),
        "max_combined_put_mark_fraction": float(frame["combined_put_mark_fraction"].max()),
        "min_cash_weight_raw": float(frame["sleeve_matched_dynamic_put_cash_weight_raw"].min()),
        "negative_cash_days": int(frame["sleeve_matched_dynamic_put_cash_weight_raw"].lt(0).sum()),
        "combined_put_active_days": int(frame["combined_put_qty"].gt(0).sum()),
        "combined_put_at_cap_days": int(frame["combined_put_qty"].eq(4.0).sum()),
        "identities": identities,
    }
    return frame, audit


def metric_values(returns: pd.Series) -> dict[str, float]:
    nav = (1.0 + returns.astype(float)).cumprod()
    ann_return = float(nav.iloc[-1] ** (252.0 / len(returns)) - 1.0)
    ann_vol = float(returns.std(ddof=0) * math.sqrt(252.0))
    drawdown = nav / nav.cummax() - 1.0
    return {
        "rows": int(len(returns)),
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe_repo": ann_return / ann_vol if ann_vol > 1e-12 else 0.0,
        "max_dd": float(drawdown.min()),
        "final_nav": float(nav.iloc[-1]),
    }


def build_window_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    end = frame["date"].max()
    rows: list[dict[str, object]] = []
    for strategy, column in STRATEGIES.items():
        for window, offset in WINDOWS:
            requested = None if offset is None else end - offset
            sample = frame if requested is None else frame[frame["date"].ge(requested)]
            available = bool(
                requested is None
                or sample["date"].min() <= requested + pd.Timedelta(days=7)
            )
            values = metric_values(sample[column]) if available else {
                key: np.nan
                for key in ("rows", "ann_return", "ann_vol", "sharpe_repo", "max_dd", "final_nav")
            }
            rows.append(
                {
                    "strategy": strategy,
                    "window": window,
                    "available": available,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    "proxy_rows": int(sample["put_source"].eq("theoretical_csi1000_put").sum()),
                    "real_rows": int(sample["put_source"].eq("real_mo_frozen_v2").sum()),
                    **values,
                }
            )
    return pd.DataFrame(rows)


def build_phase_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    phases = {
        "theoretical_put_proxy": frame["put_source"].eq("theoretical_csi1000_put"),
        "real_im_mo": frame["put_source"].eq("real_mo_frozen_v2"),
        "2015_available": frame["date"].dt.year.eq(2015),
    }
    rows: list[dict[str, object]] = []
    for phase, mask in phases.items():
        sample = frame[mask]
        for strategy, column in STRATEGIES.items():
            rows.append(
                {
                    "phase": phase,
                    "strategy": strategy,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    **metric_values(sample[column]),
                }
            )
    return pd.DataFrame(rows)


def build_annual(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year, sample in frame.groupby(frame["date"].dt.year):
        for strategy, column in STRATEGIES.items():
            rows.append(
                {
                    "year": int(year),
                    "strategy": strategy,
                    "start": sample["date"].min().date().isoformat(),
                    "end": sample["date"].max().date().isoformat(),
                    **metric_values(sample[column]),
                }
            )
    return pd.DataFrame(rows)


def build_drawdowns(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for strategy in STRATEGIES:
        nav = frame[f"{strategy}_nav"]
        dd = frame[f"{strategy}_drawdown"]
        trough = dd.idxmin()
        peak = nav.loc[:trough].idxmax()
        rows.append(
            {
                "strategy": strategy,
                "peak": frame.loc[peak, "date"].date().isoformat(),
                "trough": frame.loc[trough, "date"].date().isoformat(),
                "max_dd": float(dd.loc[trough]),
                "recovered_by_end": bool(nav.iloc[-1] >= nav.loc[peak]),
            }
        )
    return pd.DataFrame(rows)


def pct(value: object) -> str:
    return "N/A" if pd.isna(value) else f"{100.0 * float(value):.2f}%"


def write_record(
    frame: pd.DataFrame,
    window: pd.DataFrame,
    phase: pd.DataFrame,
    drawdowns: pd.DataFrame,
    audit: dict[str, object],
) -> None:
    labels = {
        "no_put": "不加Put",
        "sleeve_matched_dynamic_put": "逐袖同比例动态Put（正确主路径）",
    }
    table = ["|路径|全样本|近10年|近5年|近3年|近1年|", "|---|---:|---:|---:|---:|---:|"]
    for strategy, label in labels.items():
        block = window[window["strategy"].eq(strategy)].set_index("window")
        cells = [
            f"{pct(block.loc[name, 'ann_return'])} / {pct(block.loc[name, 'max_dd'])}"
            for name, _ in WINDOWS
        ]
        table.append(f"|{label}|{'|'.join(cells)}|")

    phase_rows = ["|分层|路径|年化收益|最大回撤|", "|---|---|---:|---:|"]
    for phase_name, phase_label in (
        ("theoretical_put_proxy", "理论Put/代理IM"),
        ("real_im_mo", "真实IM/MO"),
    ):
        for strategy, label in labels.items():
            row = phase[
                phase["phase"].eq(phase_name) & phase["strategy"].eq(strategy)
            ].iloc[0]
            phase_rows.append(f"|{phase_label}|{label}|{pct(row.ann_return)}|{pct(row.max_dd)}|")

    dd_rows = ["|路径|峰值日|谷底日|最大回撤|", "|---|---:|---:|---:|"]
    for row in drawdowns.itertuples(index=False):
        dd_rows.append(f"|{labels[row.strategy]}|{row.peak}|{row.trough}|{pct(row.max_dd)}|")

    text = f"""# 50%裸滚IM + 50%动量IM：逐袖同比例动态Put v3

状态：用户纠正口径后研究完成；未批准实盘  
共同数据截止：{audit['end']}

## 1. Scope

- 裸滚0.5倍IM始终配置0.5倍正常Put。
- 动量袖持有0/0.25/0.5倍IM时，分别配置0/0.25/0.5倍正常Put。
- 合计Put等于原V2目标0至4张乘当日总IM名义0.5/0.75/1，最高4张。

## 2. Key Results

每格为年化收益 / 最大回撤。

{chr(10).join(table)}

### 理论层与真实层

{chr(10).join(phase_rows)}

## 3. 最大回撤区间

{chr(10).join(dd_rows)}

## 4. 袖级仓位与Put审计

- `momentum_weight`实际值：{audit['momentum_weight_values']}；总IM名义：{audit['total_im_unit_values']}。
- 原V2目标最高{audit['max_v2_target_put_qty']:.0f}张；裸滚袖最高{audit['max_bare_sleeve_put_qty']:.0f}张；动量袖最高{audit['max_momentum_sleeve_put_qty']:.0f}张；组合最高{audit['max_combined_put_qty']:.0f}张。
- 组合Put最大名义为净资产{audit['max_combined_put_notional_fraction']:.2f}倍，对应原规则设计下约1 Delta保护；没有超过4张。
- 最大Put市值占净资产{audit['max_combined_put_mark_fraction']:.2%}；最低现金{audit['min_cash_weight_raw']:.2%}；负现金日{audit['negative_cash_days']}。
- 裸滚袖Put + 动量袖Put = 组合Put的张数、损益、费用、市值、名义恒等式最大误差均不超过{max(audit['identities'].values()):.3e}。

## 5. Code and Data Provenance

- 输入：冻结v2逐日文件，其底层真实Put来自`outputs/ic_im_system_mainlines_v2/daily_candidates.csv.gz`；理论Put沿用已审计CSI1000模型。
- 样本{audit['start']}至{audit['end']}：理论Put/代理IM {audit['proxy_rows']}日，真实IM/MO {audit['real_rows']}日；Asia/Shanghai交易日历。
- 价格指数不复权；动量Put开关跟随对应IM袖的T+1实际目标权重。

## 6. Execution and Frictions

- 约3个月、95%目标行权价；T日信号、T+1收盘执行；Put费用、期货换月和交易成本均计入。
- 每1倍IM使用30%保证金及缓冲，剩余现金按年化3%；Put市值扣减现金。
- Call与网格关闭；未计盘口价差、冲击、收盘容量、动态保证金、价格限制和整数合约误差。

## 7. Integrity Checks

- 与此前`put_scaled_total_exposure`逐日收益复现误差{audit['identities']['prior_total_exposure_path_parity_max_abs']:.3e}；此前数值正确，但路径分类错误。
- 组合Put不超过4张；裸滚与动量各不超过2张；现金非负。
- 本版只纠正研究解释与主路径身份，不触碰生产主线。

## 8. Risks and Caveats

- 上市前贴水由上市后均值回填，Put为理论定价，不能视为2015年真实可执行回测。
- 归一化张数允许0.5/0.75等分数，实际下单必须按组合资本规模映射为整数合约。

## 9. Backup and Rollback

- v2备份：`.codex_backups/20260823_092725/`。
- v3只新增独立规格、脚本和输出；回滚为停用v3并保留既有证据。

## Decision

`correct_sleeve_matched_dynamic_put_research_only_not_live_approved`。
"""
    (OUTPUT / "record.md").write_text(text, encoding="utf-8")


def run() -> None:
    hashes = verify_inputs()
    frame, audit = build_daily()
    window = build_window_metrics(frame)
    phase = build_phase_metrics(frame)
    annual = build_annual(frame)
    drawdowns = build_drawdowns(frame)

    OUTPUT.mkdir(parents=True, exist_ok=False)
    frame.to_csv(OUTPUT / "daily_nav.csv.gz", index=False, compression="gzip", encoding="utf-8-sig")
    window.to_csv(OUTPUT / "metrics_by_window.csv", index=False, encoding="utf-8-sig")
    phase.to_csv(OUTPUT / "phase_metrics.csv", index=False, encoding="utf-8-sig")
    annual.to_csv(OUTPUT / "annual_metrics.csv", index=False, encoding="utf-8-sig")
    drawdowns.to_csv(OUTPUT / "drawdown_episodes.csv", index=False, encoding="utf-8-sig")
    (OUTPUT / "diagnostics.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_status": "corrected_sleeve_matched_put_research_only_not_live",
        "command": f"{Path(sys.executable).name} {Path(__file__).name}",
        "script_sha256": sha256(Path(__file__)),
        "input_hashes": hashes,
        "sample": {
            "start": audit["start"], "end": audit["end"], "rows": audit["rows"],
            "proxy_rows": audit["proxy_rows"], "real_rows": audit["real_rows"],
            "timezone": "Asia/Shanghai",
        },
        "main_variant": {
            "name": "sleeve_matched_dynamic_put",
            "bare_put_scale": 0.5,
            "momentum_put_scale": "0.5 * momentum_weight",
            "combined_put_scale": "total_im_units",
            "combined_put_cap": 4.0,
            "call": "off", "grid": "off",
        },
        "diagnostics": audit,
    }
    (OUTPUT / "data_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "command_log.txt").write_text(manifest["command"] + "\n", encoding="utf-8")
    write_record(frame, window, phase, drawdowns, audit)


if __name__ == "__main__":
    run()
