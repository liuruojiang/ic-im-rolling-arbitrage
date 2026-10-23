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
VERSION = "im_roll50_momentum50_fullcycle_put_v2"
SPEC = ROOT / "docs" / f"{VERSION}_spec.md"
SPEC_HASH = ROOT / "docs" / f"{VERSION}_spec.md.sha256"
V1_OUTPUT = ROOT / "outputs" / "im_roll50_momentum50_fullcycle_put_v1"
V1_DAILY = V1_OUTPUT / "daily_nav.csv.gz"
V1_STATE = V1_OUTPUT / "put_signal_state.csv.gz"
V1_SCHEDULE = V1_OUTPUT / "theoretical_put_schedule.csv.gz"
OUTPUT = ROOT / "outputs" / VERSION
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
    "put_half_scaled": "put_half_scaled_ret",
    "put_original_v2_dynamic": "put_original_v2_dynamic_ret",
    "put_scaled_total_exposure": "put_scaled_total_exposure_ret",
}
PINNED_HASHES = {
    V1_DAILY: "2d858c1f1eb2e5b45166af637386ece40736554f9c7e18c486c0dba7bce0e44f",
    V1_STATE: "d3d97ad62a384db8483547fdefed64ede34d84473ca0602290757d5cecfc8495",
    V1_SCHEDULE: "9b565d7ddc2976a652946da788899275e4f26b66e4bf6577ed0c66b635e1c628",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_inputs() -> dict[str, str]:
    for path in (SPEC, SPEC_HASH, *PINNED_HASHES):
        if not path.exists():
            raise FileNotFoundError(path)
    expected_spec = SPEC_HASH.read_text(encoding="utf-8").split()[0].lower()
    actual_spec = sha256(SPEC)
    if actual_spec != expected_spec:
        raise RuntimeError(f"Specification hash mismatch: {actual_spec} != {expected_spec}")
    actual = {path: sha256(path) for path in PINNED_HASHES}
    for path, expected in PINNED_HASHES.items():
        if actual[path] != expected:
            raise RuntimeError(f"Pinned v1 input hash mismatch: {path}")
    if OUTPUT.exists():
        raise FileExistsError(f"Formal output already exists: {OUTPUT}")
    return {
        str(SPEC.relative_to(ROOT)): actual_spec,
        **{str(path.relative_to(ROOT)): value for path, value in actual.items()},
    }


def build_daily() -> tuple[pd.DataFrame, dict[str, object]]:
    frame = pd.read_csv(V1_DAILY, parse_dates=["date"], low_memory=False)
    frame["put_half_scaled_ret"] = frame["put_fixed_0p5_core_ret"]
    frame["put_half_scaled_put_scale"] = 0.5
    frame["put_half_scaled_put_qty"] = frame["put_fraction"]

    label = "put_original_v2_dynamic"
    frame[f"{label}_put_scale"] = 1.0
    frame[f"{label}_put_qty"] = 2.0 * frame["put_fraction"]
    frame[f"{label}_put_notional_fraction"] = frame["put_fraction"]
    frame[f"{label}_put_pnl_ret"] = frame["put_pnl_ret"]
    frame[f"{label}_put_cost_rate"] = frame["put_cost_rate"]
    frame[f"{label}_put_mark_fraction"] = frame["put_mark_fraction"]
    frame[f"{label}_pre_cash_ret"] = (
        (1.0 + frame["baseline_pre_cash_ret"] + frame["put_pnl_ret"])
        * (1.0 - frame["put_cost_rate"])
        - 1.0
    )
    frame[f"{label}_cash_weight_raw"] = (
        frame["blend_cash_weight"] - frame["put_mark_fraction"]
    )
    if frame[f"{label}_cash_weight_raw"].lt(-1e-12).any():
        raise RuntimeError("Original V2 Put mark exceeds available cash")
    frame[f"{label}_cash_weight"] = frame[f"{label}_cash_weight_raw"].clip(lower=0.0)
    frame[f"{label}_ret"] = (
        frame[f"{label}_pre_cash_ret"]
        + frame[f"{label}_cash_weight"] * CASH_DAILY
    )
    frame[f"{label}_put_notional_to_im"] = np.where(
        frame["total_im_units"].gt(0),
        frame[f"{label}_put_notional_fraction"] / frame["total_im_units"],
        np.nan,
    )

    allowed_qty = {0.0, 1.0, 2.0, 3.0, 4.0}
    if not set(frame[f"{label}_put_qty"].round(12).unique()).issubset(allowed_qty):
        raise RuntimeError("Original V2 Put quantity is not in 0..4 integer ladder")
    if frame[f"{label}_ret"].isna().any() or frame[f"{label}_ret"].le(-1.0).any():
        raise RuntimeError("Invalid original V2 dynamic Put returns")

    for strategy, column in STRATEGIES.items():
        frame[f"{strategy}_nav"] = (1.0 + frame[column]).cumprod()
        frame[f"{strategy}_drawdown"] = (
            frame[f"{strategy}_nav"] / frame[f"{strategy}_nav"].cummax() - 1.0
        )

    expected_pre_cash = (
        (1.0 + frame["baseline_pre_cash_ret"] + frame["put_pnl_ret"])
        * (1.0 - frame["put_cost_rate"])
        - 1.0
    )
    expected_ret = expected_pre_cash + frame[f"{label}_cash_weight"] * CASH_DAILY
    recomposition_error = float((frame[f"{label}_ret"] - expected_ret).abs().max())
    if recomposition_error > 1e-14:
        raise RuntimeError(f"Original V2 recomposition failed: {recomposition_error}")

    qty = frame[f"{label}_put_qty"].round().astype(int)
    ratio = frame[f"{label}_put_notional_to_im"]
    audit = {
        "start": frame["date"].min().date().isoformat(),
        "end": frame["date"].max().date().isoformat(),
        "rows": int(len(frame)),
        "proxy_rows": int(frame["put_source"].eq("theoretical_csi1000_put").sum()),
        "real_rows": int(frame["put_source"].eq("real_mo_frozen_v2").sum()),
        "recomposition_max_abs_error": recomposition_error,
        "put_qty_counts": {str(key): int(value) for key, value in qty.value_counts().sort_index().items()},
        "put_active_days": int(qty.gt(0).sum()),
        "put_four_days": int(qty.eq(4).sum()),
        "max_put_mark_fraction": float(frame[f"{label}_put_mark_fraction"].max()),
        "min_cash_weight_raw": float(frame[f"{label}_cash_weight_raw"].min()),
        "max_put_notional_fraction": float(frame[f"{label}_put_notional_fraction"].max()),
        "max_put_notional_to_im": float(ratio.max()),
        "days_put_notional_above_im": int(ratio.gt(1.0 + 1e-12).sum()),
        "days_put_notional_at_least_2x_im": int(ratio.ge(2.0 - 1e-12).sum()),
        "negative_cash_days": int(frame[f"{label}_cash_weight_raw"].lt(0).sum()),
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
    periods = {
        "theoretical_put_proxy": frame["put_source"].eq("theoretical_csi1000_put"),
        "real_im_mo": frame["put_source"].eq("real_mo_frozen_v2"),
        "2015_available": frame["date"].dt.year.eq(2015),
    }
    rows: list[dict[str, object]] = []
    for phase, mask in periods.items():
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
        "put_half_scaled": "动态Put整体乘0.5（旧诊断）",
        "put_original_v2_dynamic": "原V2动态Put 0–4张（本版主路径）",
        "put_scaled_total_exposure": "动态Put按总IM敞口缩放（诊断）",
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
    phase_labels = {"theoretical_put_proxy": "理论Put/代理IM", "real_im_mo": "真实IM/MO"}
    for phase_name in phase_labels:
        for strategy in ("no_put", "put_half_scaled", "put_original_v2_dynamic"):
            row = phase[
                phase["phase"].eq(phase_name) & phase["strategy"].eq(strategy)
            ].iloc[0]
            phase_rows.append(
                f"|{phase_labels[phase_name]}|{labels[strategy]}|"
                f"{pct(row.ann_return)}|{pct(row.max_dd)}|"
            )

    dd_rows = ["|路径|峰值日|谷底日|最大回撤|", "|---|---:|---:|---:|"]
    for row in drawdowns.itertuples(index=False):
        dd_rows.append(
            f"|{labels[row.strategy]}|{row.peak}|{row.trough}|{pct(row.max_dd)}|"
        )

    main_full = window[
        window["strategy"].eq("put_original_v2_dynamic") & window["window"].eq("full")
    ].iloc[0]
    no_full = window[window["strategy"].eq("no_put") & window["window"].eq("full")].iloc[0]
    main_real = phase[
        phase["strategy"].eq("put_original_v2_dynamic") & phase["phase"].eq("real_im_mo")
    ].iloc[0]
    no_real = phase[phase["strategy"].eq("no_put") & phase["phase"].eq("real_im_mo")].iloc[0]

    text = f"""# 50%滚IM + 50%动量门控：原V2动态Put完整叠加 v2

状态：研究完成；未批准实盘  
共同数据截止：{frame['date'].max().date().isoformat()}

## 1. Scope

- 本版主路径不再把Put乘0.5，而是按原V2估值/MOM120规则动态持有0至4张。
- 固定底仓仍为50%滚IM + 50%动量门控；Call与网格关闭。

## 2. Key Results

每格为年化收益 / 最大回撤。

{chr(10).join(table)}

原V2完整动态Put相对不加Put：全样本年化变化{(main_full.ann_return-no_full.ann_return)*100:+.2f}个百分点，最大回撤变化{(main_full.max_dd-no_full.max_dd)*100:+.2f}个百分点；真实IM/MO期年化变化{(main_real.ann_return-no_real.ann_return)*100:+.2f}个百分点，最大回撤变化{(main_real.max_dd-no_real.max_dd)*100:+.2f}个百分点。

### 理论层与真实层

{chr(10).join(phase_rows)}

## 3. 最大回撤区间

{chr(10).join(dd_rows)}

## 4. Put张数与资本

- 动态Put实际张数分布：{json.dumps(audit['put_qty_counts'], ensure_ascii=False)}；持有Put {audit['put_active_days']}日，其中4张 {audit['put_four_days']}日。
- 最大Put名义为净资产{audit['max_put_notional_fraction']:.2f}倍；相对当日IM底仓最高{audit['max_put_notional_to_im']:.2f}倍。
- Put名义超过当日IM底仓共{audit['days_put_notional_above_im']}日，达到或超过2倍底仓共{audit['days_put_notional_at_least_2x_im']}日。
- 最大Put市值占净资产{audit['max_put_mark_fraction']:.2%}；最低剩余现金{audit['min_cash_weight_raw']:.2%}；负现金日{audit['negative_cash_days']}。

## 5. Code and Data Provenance

- 输入为v1已审计逐日底仓及理论/真实Put组件；Put信号仍来自绝对估值、57个月相对估值和`MOM120 < 0`最低4张。
- 样本{audit['start']}至{audit['end']}，理论Put/代理IM {audit['proxy_rows']}日，真实IM/MO {audit['real_rows']}日；Asia/Shanghai交易日历。
- 价格指数不复权；MOM120使用中证1000全收益指数；真实IM/MO使用中金所冻结官方收盘路径。

## 6. Execution and Frictions

- T日收盘信号、T+1收盘执行；3个月目标期限、95%目标行权价；Put费用和底仓期货费用已计入。
- 30%/倍IM保证金缓冲，其余现金年化3%；Put市值逐日扣减现金。
- 未计盘口价差、冲击、收盘容量、动态保证金、价格限制和组合规模整数误差。

## 7. Integrity Checks

- 原V2完整Put逐日收益重组最大误差{audit['recomposition_max_abs_error']:.3e}。
- Put张数仅出现0/1/2/3/4；共同日历与v1冻结路径完全一致。
- 本版不触碰V2生产主线，不代表当前信号或下单授权。

## 8. Risks and Caveats

- 2015至2022年为理论Put和平均贴水代理，含明确前视与模型风险。
- 在底仓只有0.5倍IM时仍保留原0至4张Put，会出现显著超额保护；这是用户指定的直接叠加口径，不等于风险匹配后的最优仓位。

## 9. Backup and Rollback

- v1备份：`.codex_backups/20260822_234149/`。
- 本版只新增独立v2脚本、规格与输出；回滚方式为停用v2路径并保留v1。

## Decision

`research_corrected_full_dynamic_put_not_live_approved`。
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
        "research_status": "corrected_full_dynamic_put_research_only_not_live",
        "command": f"{Path(sys.executable).name} {Path(__file__).name}",
        "script_sha256": sha256(Path(__file__)),
        "input_hashes": hashes,
        "sample": {
            "start": audit["start"], "end": audit["end"], "rows": audit["rows"],
            "proxy_rows": audit["proxy_rows"], "real_rows": audit["real_rows"],
            "timezone": "Asia/Shanghai",
        },
        "main_variant": {
            "name": "put_original_v2_dynamic",
            "put_scale": 1.0,
            "target_qty": "dynamic 0/1/2/3/4 from frozen V2 rule",
            "call": "off",
            "grid": "off",
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
