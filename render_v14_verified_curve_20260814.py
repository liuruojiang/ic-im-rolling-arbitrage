"""Render the verified, single-rule v1.4 real-listed-options NAV through its data cutoff."""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "v14_full_history_real_options_verified_20260814"
IC = ROOT / "outputs" / "v1_4_fix_20260917" / "historical_rerun" / "ic" / "daily_outputs" / "daily.csv.gz"
IM = ROOT / "quant_param_scan_runs" / "20260919_ic_im_im_v1_4_r1_full_joint_im_core_and_momentum_long_put_mom120_floor_2_vs_3" / "daily.csv.gz"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=False)
    ic = pd.read_csv(IC, parse_dates=["date"])
    ic = ic[(ic.scope == "real") & (ic.variant == "final_joint")].loc[:, ["date", "return_net"]]
    ic = ic.rename(columns={"return_net": "IC_return"}).sort_values("date")
    im = pd.read_csv(IM, compression="gzip", parse_dates=["date"])
    im = im[(im.scope == "real") & (im.candidate == "real_floor3")].loc[:, ["date", "ret"]]
    im = im.rename(columns={"ret": "IM_return"}).sort_values("date")
    frame = ic.merge(im, on="date", how="inner", validate="one_to_one")
    start = frame.date.max() - pd.DateOffset(years=1)
    frame = frame[frame.date >= start].copy()
    frame["IC_nav"] = (1 + frame.IC_return).cumprod()
    frame["IM_nav"] = (1 + frame.IM_return).cumprod()
    frame.to_csv(OUT / "nav_1y.csv", index=False, encoding="utf-8-sig")
    fig, ax = plt.subplots(figsize=(12, 7), facecolor="#f8fafc")
    ax.set_facecolor("#f8fafc")
    for column, label, color in [("IC_nav", "IC v1.4", "#2563a8"), ("IM_nav", "IM v1.4", "#d97716")]:
        ax.plot(frame.date, frame[column], label=f"{label}  {frame[column].iat[-1]-1:+.1%}", lw=2.5, color=color)
    ax.axhline(1, color="#94a3b8", ls="--", lw=1)
    ax.grid(axis="y", alpha=.35)
    ax.legend(frameon=False, loc="upper left")
    ax.set_ylabel("NAV (start = 1)")
    ax.set_title("IC / IM v1.4 unified-rule NAV — real listed-options layer", weight="bold")
    fig.text(.10, .035, "Single v1.4 counterfactual rule set. Verified real-options data cutoff: 2026-08-14. Not extended with older-version rules.", fontsize=9, color="#475569")
    fig.subplots_adjust(left=.09, right=.96, top=.88, bottom=.14)
    fig.savefig(OUT / "nav_1y.png", dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)
    (OUT / "record.md").write_text(
        "# v1.4 unified-rule verified curve\n\n"
        "- Rule: current v1.4 rule set applied across the full history (counterfactual research).\n"
        "- IC source: v1.4 final-joint, real listed 510500-options layer.\n"
        "- IM source: v1.4 full-joint real listed MO-options layer, MOM120 floor 3.\n"
        "- Cutoff: 2026-08-14. This artifact deliberately does not append a different strategy version.\n"
        "- `nav_1y.png` and `nav_1y.csv` contain the matched final-year window.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
