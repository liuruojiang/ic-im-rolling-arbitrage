"""Attach the first v1.4 verified session to the freshly rebuilt r7 history."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import refresh_nav_r7_complete_20260911 as replay
import poe_ic_im_mainline_v1_3_bot as market_source


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "outputs" / "nav_versioned_refresh_20260920_final"
OUTPUT = ROOT / "outputs" / "nav_versioned_refresh_20260920_v14_corrected_final2"
V14_DAY = date(2026, 9, 18)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fresh_benchmark(path: Path, end: date) -> pd.Series:
    product = "IC" if path == replay.IC_BENCHMARK else "IM"
    frame = market_source.fetch_ohlcv_history(product)
    values = pd.to_numeric(frame["close"], errors="raise")
    result = pd.Series(values.to_numpy(dtype=float), index=pd.DatetimeIndex(frame.index))
    result = result[~result.index.duplicated(keep="last")].sort_index()
    if result.index[-1].date() < end:
        raise RuntimeError(f"fresh {product} benchmark ends at {result.index[-1].date()}, before {end}")
    return result


def render_clean_chart() -> None:
    """Render a readable chart without inheriting the legacy r7 title/template."""
    frame = pd.read_csv(OUTPUT / "nav_1y.csv", parse_dates=["date"])
    fig, ax = plt.subplots(figsize=(12, 7), facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")
    lines = (("IC", "IC_nav", "#2563a8"), ("IM", "IM_nav", "#d97716"))
    for name, column, color in lines:
        values = pd.to_numeric(frame[column], errors="raise")
        total = values.iloc[-1] - 1.0
        ax.plot(frame["date"], values, lw=2.5, color=color, label=f"{name}  |  return {total:+.1%}")
        ax.annotate(f"{values.iloc[-1]:.3f}", (frame["date"].iloc[-1], values.iloc[-1]), xytext=(10, 0), textcoords="offset points", color=color, va="center", weight="bold")
    ax.axhline(1.0, color="#94a3b8", ls="--", lw=1)
    ax.grid(axis="y", color="#cbd5e1", alpha=.7)
    ax.set_title("IC / IM Versioned NAV · Last 1 Year\n2025-09-18 to 2026-09-18", fontsize=17, weight="bold")
    ax.set_ylabel("NAV (start = 1.0)")
    ax.legend(frameon=False, loc="upper left")
    fig.text(.10, .035, "Rules: r7 through 2026-09-17; v1.4-r1 fix3 on 2026-09-18.  Research replay, not account NAV.", color="#475569", fontsize=9.5)
    fig.subplots_adjust(left=.09, right=.92, top=.84, bottom=.14)
    fig.savefig(OUTPUT / "nav_1y.png", dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)


def correct_v14_boundary(ic: pd.DataFrame, im: pd.DataFrame, previous: dict, current: dict, strategy) -> None:
    """Value 2026-09-18 from the preceding v1.4 ledger legs, never a replaced strike."""
    marks = strategy.fetch_cffex_daily_marks(
        ["IC2612", "IM2612", "MO2612-P-7600"], V14_DAY, V14_DAY
    ).reset_index().set_index("contract")
    ic_quote = marks.loc["IC2612"]
    im_quote = marks.loc["IM2612"]
    old_ic = previous["products"]["IC"]
    new_ic = current["signals"]["IC"]
    old_ic_mark = float(strategy.fetch_option_closes(str(old_ic["post_put_security_id"])).loc[str(V14_DAY)])
    new_ic_mark = float(strategy.fetch_option_closes(str(new_ic["put_target_security_id"])).loc[str(V14_DAY)])
    ic_pre, ic_eod = float(ic_quote["pre_settle"]), float(ic_quote["settle"])
    ic_current_qty, ic_target_qty = float(old_ic["post_put_qty"]), float(new_ic["put_target_total_qty"])
    ic_fut = 0.5 * (ic_eod / ic_pre - 1.0)
    ic_put = ic_current_qty * 10_000.0 * (old_ic_mark - float(strategy.fetch_option_closes(str(old_ic["post_put_security_id"])).loc["2026-09-17"])) / (ic_pre * 200.0)
    ic_cost = (ic_current_qty / 20.0 + ic_target_qty / 20.0) * replay.ONE_WAY
    ic_fraction = ic_target_qty * 10_000.0 * new_ic_mark / (ic_eod * 200.0)
    ic_cash = max(0.0, 1.0 - replay.MARGIN * 0.5 - ic_fraction)
    ic_return = (1.0 + ic_fut + ic_put) * (1.0 - ic_cost) - 1.0 + ic_cash * replay.CASH_DAILY_IC
    for key, value in {"ret": ic_return, "futures_gross_ret": ic_fut, "futures_cost_rate": 0.0, "put_pnl_ret": ic_put, "put_cost_rate": ic_cost, "put_mark_fraction": ic_fraction, "cash_weight": ic_cash, "put_qty": ic_target_qty, "put_contract": new_ic["put_target_contract"]}.items():
        ic.loc[ic.index[-1], key] = value

    old_im = previous["products"]["IM"]
    im_pre, im_eod = float(im_quote["pre_settle"]), float(im_quote["settle"])
    im_mark = float(marks.loc["MO2612-P-7600", "settle"])
    im_previous_mark = float(marks.loc["MO2612-P-7600", "pre_settle"])
    im_qty = float(previous["signals"]["IM"]["core_put_current_qty_normalized"]) + float(
        previous["signals"]["IM"].get("momentum_put_current_qty_normalized", 0.0)
    )
    im_fut = 0.5 * (im_eod / im_pre - 1.0)
    im_put = 0.5 * im_qty * (im_mark - im_previous_mark) / im_pre
    im_fraction = 0.5 * im_qty * im_mark / im_eod
    im_cash = max(0.0, 1.0 - replay.MARGIN * 0.5 - im_fraction)
    im_return = 1.0 + im_fut + im_put - 1.0 + im_cash * replay.CASH_DAILY_IM
    for key, value in {"ret": im_return, "futures_gross_ret": im_fut, "futures_cost_rate": 0.0, "put_pnl_ret": im_put, "put_cost_rate": 0.0, "put_mark_fraction": im_fraction, "cash_weight": im_cash, "put_qty_normalized": im_qty, "core_put_contract": "MO2612-P-7600"}.items():
        im.loc[im.index[-1], key] = value


def main() -> None:
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise FileExistsError(f"output already exists: {OUTPUT}")
    OUTPUT.mkdir(parents=True)
    replay.END = V14_DAY
    replay.OUTPUT = OUTPUT
    replay._load_benchmark = load_fresh_benchmark
    signals = json.loads((SOURCE / "historical_signals.json").read_text(encoding="utf-8"))
    journal = json.loads(
        (ROOT / "runtime" / "ic_im_v1_4_r1" / "journal" / "000002-2026-09-18.json").read_text(
            encoding="utf-8"
        )
    )
    signals[V14_DAY.isoformat()] = journal["signals"]
    ordered = {key: signals[key] for key in sorted(signals)}
    (OUTPUT / "historical_signals.json").write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    strategy = replay.load_replay_strategy()
    ic, im, tail_audit = replay.build_tail_nav(ordered, strategy)
    previous = json.loads(
        (ROOT / "runtime" / "ic_im_v1_4_r1" / "journal" / "000001-2026-09-17.json").read_text(
            encoding="utf-8"
        )
    )
    correct_v14_boundary(ic, im, previous, journal, strategy)
    ic.to_csv(OUTPUT / "ic_tail_daily.csv", index=False, encoding="utf-8-sig")
    im.to_csv(OUTPUT / "im_tail_daily.csv", index=False, encoding="utf-8-sig")
    sources = [
        Path(replay.base_strategy.__file__).resolve(),
        replay.IC_FORMAL,
        replay.IM_FORMAL,
        ROOT / "runtime" / "ic_im_v1_4_r1" / "journal" / "000002-2026-09-18.json",
    ]
    result = replay.draw_and_save(
        ic,
        im,
        {"version_boundary": "r7 through 2026-09-17; v1.4-r1 fix3 on 2026-09-18", **tail_audit},
        {str(path.relative_to(ROOT)): sha256(path) for path in sources},
    )
    verification = {
        "status": "research_only_versioned_historical_replay",
        "end": V14_DAY.isoformat(),
        "version_boundary": "r7 through 2026-09-17; v1.4-r1 fix3 on 2026-09-18",
        "latest_v14_ledger_sequence": journal["sequence"],
        "latest_v14_ledger_digest": journal["digest"],
        "metrics": result["metrics"],
        "tail_audit": tail_audit,
        "source_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in sources},
    }
    (OUTPUT / "verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    lines = [
        "# IC / IM 近一年版本化净值刷新",
        "",
        "状态：`research_only_versioned_historical_replay`。",
        "",
        "- 数据截止：`2026-09-18`（最近完成交易日）。",
        "- 版本边界：至2026-09-17按当日有效r7规则；2026-09-18按v1.4-r1 fix3已核验账本。",
        "- 数据：重新下载并校验中金所期货/期权历史收盘及指数OHLCV；不使用旧净值CSV续接。",
        "- 每1倍期货30%保证金/缓冲、余款年化3%，含脚本既有期货与期权成本；不含额外冲击、容量、动态保证金或账户整数映射。",
        "",
        "## 最近一年",
        "",
    ]
    for row in result["metrics"]:
        if row["years"] == 1:
            lines.append(
                f"- {row['product']}：净值 `{row['final_nav']:.6f}`，区间收益 `{row['total_return']:.2%}`，年化 `{row['ann_return']:.2%}`，最大回撤 `{row['max_dd']:.2%}`，截止 `{row['end']}`。"
            )
    lines.extend(["", "## 文件", "", "- `nav_1y.png`：近一年净值曲线。", "- `nav_1y.csv`：曲线数据。", "- `verification.json`：版本、数据与哈希核验。"])
    (OUTPUT / "record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    render_clean_chart()
    print(json.dumps({"output": str(OUTPUT), "metrics": result["metrics"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
