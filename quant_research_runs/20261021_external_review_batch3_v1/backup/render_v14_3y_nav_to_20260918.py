"""Render the latest three-year IC/IM NAV using one unified v1.4 rule set."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "v14_fix3_3y_nav_to_20260918_final"
START = pd.Timestamp("2023-09-18")
END = pd.Timestamp("2026-09-18")
CASH = 1.03 ** (1 / 252) - 1
IC_FIX3_SCALE = 1.5384615384615385

IC_BASE = ROOT / "quant_param_scan_runs" / "20260918_ic_im_ic_rebuild_l11_3x_joint_neighborhood_iv275_300_325_x_q1_qd05" / "daily_outputs" / "daily.csv.gz"
IM_BASE = ROOT / "quant_param_scan_runs" / "20260919_ic_im_im_v1_4_r1_full_joint_im_core_and_momentum_long_put_mom120_floor_2_vs_3" / "daily.csv.gz"
TAIL_DIR = ROOT / "outputs" / "v14_full_nav_refresh_20260920_final"
IC_TAIL = TAIL_DIR / "ic_tail_daily.csv"
IM_TAIL = TAIL_DIR / "im_tail_daily.csv"
TAIL_VERIFY = TAIL_DIR / "verification.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(nav: pd.Series, dates: pd.Series) -> dict[str, float | str]:
    drawdown = nav / nav.cummax() - 1
    trough = int(drawdown.idxmin())
    peak = int(nav.loc[:trough].idxmax())
    years = (dates.iloc[-1] - dates.iloc[0]).days / 365.2425
    return {
        "total_return": float(nav.iloc[-1] - 1),
        "cagr": float(nav.iloc[-1] ** (1 / years) - 1),
        "max_drawdown": float(drawdown.iloc[trough]),
        "drawdown_peak": str(dates.iloc[peak].date()),
        "drawdown_trough": str(dates.iloc[trough].date()),
    }


def main() -> None:
    if OUT.exists() and any(OUT.iterdir()):
        raise FileExistsError(OUT)
    OUT.mkdir(parents=True)

    tail_verification = json.loads(TAIL_VERIFY.read_text(encoding="utf-8"))
    if tail_verification["status"] != "v1_4_single_rule_counterfactual_latest_actual_data":
        raise RuntimeError("tail is not the verified v1.4 continuation")
    if tail_verification["checks"]["no_old_version_return_tail"] is not True:
        raise RuntimeError("tail verification does not exclude old-version returns")
    if tail_verification["checks"]["data_end"] != str(END.date()):
        raise RuntimeError("verified v1.4 tail does not reach requested end date")

    ic_hist = pd.read_csv(IC_BASE, compression="gzip", parse_dates=["date"])
    ic_hist = ic_hist[ic_hist["candidate"] == "real_iv300_qd05_profit3x"][["date", "return_net"]]
    ic_hist = ic_hist.rename(columns={"return_net": "ret"})
    im_hist = pd.read_csv(IM_BASE, compression="gzip", parse_dates=["date"])
    im_hist = im_hist[(im_hist["scope"] == "real") & (im_hist["candidate"] == "real_floor3")][["date", "ret"]]

    ic_tail = pd.read_csv(IC_TAIL, parse_dates=["date"])
    im_tail = pd.read_csv(IM_TAIL, parse_dates=["date"])[["date", "ret", "data_layer"]]
    expected_layer = "v14_counterfactual_official_marks"
    if set(ic_tail.data_layer) != {expected_layer} or set(im_tail.data_layer) != {expected_layer}:
        raise RuntimeError("tail contains a non-v1.4 data layer")

    # The verified official-mark continuation was first rendered with the
    # earlier q1 seller size.  The latest v1.4 fix3 cycle opened on 2026-08-13
    # with q_delta05 scale 1.538461538... and remains open through 2026-09-18.
    # Scale only the fixed-router non-cash return and its 15% reserve, exactly
    # matching the frozen fix3 compose_sized accounting for this open cycle.
    fixed_router_non_cash = ic_tail["router_ret"].astype(float) - 0.7 * CASH
    ic_tail["ret"] = (
        ic_tail["ret"].astype(float)
        + 0.5 * (IC_FIX3_SCALE - 1.0) * fixed_router_non_cash
        - 0.15 * (IC_FIX3_SCALE - 1.0) * CASH
    )
    ic_tail["data_layer"] = "v14_fix3_qdelta05_from_verified_official_marks"
    ic_tail[["date", "ret", "router_ret", "data_layer"]].to_csv(
        OUT / "ic_fix3_tail_daily.csv", index=False, encoding="utf-8-sig"
    )

    ic = pd.concat([ic_hist, ic_tail[["date", "ret"]]], ignore_index=True).sort_values("date")
    im = pd.concat([im_hist, im_tail[["date", "ret"]]], ignore_index=True).sort_values("date")
    if ic.date.duplicated().any() or im.date.duplicated().any():
        raise RuntimeError("duplicate dates in v1.4 chain")

    common = ic.merge(im, on="date", suffixes=("_IC", "_IM"), validate="one_to_one")
    common = common[common.date.between(START, END)].reset_index(drop=True)
    if common.empty or common.date.iloc[0] != START or common.date.iloc[-1] != END:
        raise RuntimeError("three-year window boundary mismatch")
    if not np.isfinite(common[["ret_IC", "ret_IM"]]).all().all():
        raise RuntimeError("non-finite return in three-year window")

    # Normalize at the 2023-09-18 close.  The first displayed point is exactly 1;
    # subsequent points compound close-to-close v1.4 net returns through 2026-09-18.
    for product in ("IC", "IM"):
        gross = (1 + common[f"ret_{product}"]).cumprod()
        common[f"{product}_nav"] = gross / gross.iloc[0]
        common[f"{product}_drawdown"] = common[f"{product}_nav"] / common[f"{product}_nav"].cummax() - 1

    result_metrics = {p: metrics(common[f"{p}_nav"], common.date) for p in ("IC", "IM")}
    common.to_csv(OUT / "nav_3y.csv", index=False, encoding="utf-8-sig")

    colors = {"IC": "#2563a8", "IM": "#d97716"}
    fig, (ax, ddax) = plt.subplots(
        2, 1, figsize=(12, 9), sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1]}, facecolor="#f8fafc",
    )
    for axis in (ax, ddax):
        axis.set_facecolor("#f8fafc")
        axis.grid(axis="y", alpha=0.28)
    for product in ("IC", "IM"):
        m = result_metrics[product]
        label = f"{product} v1.4  |  Total {m['total_return']:+.1%}  |  CAGR {m['cagr']:+.1%}  |  MDD {m['max_drawdown']:.1%}"
        ax.plot(common.date, common[f"{product}_nav"], color=colors[product], lw=2.3, label=label)
        ddax.plot(common.date, common[f"{product}_drawdown"], color=colors[product], lw=1.6, label=product)
        ddax.fill_between(common.date, common[f"{product}_drawdown"], 0, color=colors[product], alpha=0.08)
    ax.axhline(1, color="#94a3b8", ls="--", lw=1)
    ddax.axhline(0, color="#94a3b8", lw=1)
    ax.set_ylabel("NAV (2023-09-18 = 1)")
    ddax.set_ylabel("Drawdown")
    ddax.yaxis.set_major_formatter(lambda value, _pos: f"{value:.0%}")
    ax.legend(frameon=False, loc="upper left", fontsize=10)
    ax.set_title("IC / IM v1.4 unified-rule NAV | 2023-09-18 to 2026-09-18", fontsize=16, weight="bold")
    ddax.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
    ddax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.text(
        0.09, 0.025,
        "One v1.4 rule set over the entire window. Real listed-options history through 2026-08-14; "
        "same-state v1.4 continuation with official marks through 2026-09-18. Research only.",
        fontsize=8.8, color="#475569",
    )
    fig.subplots_adjust(left=0.09, right=0.97, top=0.90, bottom=0.12, hspace=0.08)
    fig.savefig(OUT / "nav_3y.png", dpi=190, facecolor=fig.get_facecolor())
    plt.close(fig)

    verification = {
        "status": "verified_v1_4_three_year_unified_rule_curve",
        "window": {"start": str(common.date.iloc[0].date()), "end": str(common.date.iloc[-1].date()), "sessions": len(common)},
        "normalization": "NAV equals 1 at the 2023-09-18 close; compound subsequent net returns",
        "metrics": result_metrics,
        "sources": {
            str(IC_BASE.relative_to(ROOT)): sha256(IC_BASE),
            str(IM_BASE.relative_to(ROOT)): sha256(IM_BASE),
            str(IC_TAIL.relative_to(ROOT)): sha256(IC_TAIL),
            str(IM_TAIL.relative_to(ROOT)): sha256(IM_TAIL),
            str(TAIL_VERIFY.relative_to(ROOT)): sha256(TAIL_VERIFY),
        },
        "checks": {
            "single_v1_4_rule_set": True,
            "old_version_return_tail_used": False,
            "historical_layer": "latest v1.4 fix3 real IV30 q_delta05 profit3x path through 2026-08-14",
            "ic_tail_layer": "verified official-mark v1.4 continuation with the open 2026-08-13 seller cycle rescaled to fix3 q_delta05",
            "im_tail_layer": expected_layer,
            "ic_fix3_open_cycle_scale": IC_FIX3_SCALE,
            "date_alignment": True,
            "finite_returns": True,
        },
    }
    (OUT / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "record.md").write_text(
        "# IC / IM v1.4 最近三年净值\n\n"
        f"- 窗口：{common.date.iloc[0].date()}—{common.date.iloc[-1].date()}，{len(common)}个共同交易日。\n"
        "- 全窗口统一使用v1.4口径；未拼接任何旧版本收益。\n"
        "- 2026-08-14以前为已核验v1.4真实挂牌期权层；此后从相同v1.4状态按官方行情续接。\n"
        f"- IC：累计{result_metrics['IC']['total_return']:.2%}，CAGR {result_metrics['IC']['cagr']:.2%}，最大回撤{result_metrics['IC']['max_drawdown']:.2%}。\n"
        f"- IM：累计{result_metrics['IM']['total_return']:.2%}，CAGR {result_metrics['IM']['cagr']:.2%}，最大回撤{result_metrics['IM']['max_drawdown']:.2%}。\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(OUT), "metrics": result_metrics, "sessions": len(common)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
