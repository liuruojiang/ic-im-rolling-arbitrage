"""Descriptive real-contract quarterly spreads; no optimized trading threshold."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs/ic_im_quarter_spread_research_v1"
SOURCES = {"IC": ROOT/"data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv",
           "IM": ROOT/"data/im_monthly_roll_3m_lowest_put_v1/cffex_im_contracts.csv"}
NODES = [60, 40, 20, 10, 5, 3, 1, 0]


def build_panel(product, quotes):
    if quotes.duplicated(["contract", "date"]).any():
        raise ValueError("duplicate contract-day")
    calendar = pd.DatetimeIndex(sorted(quotes.date.unique()))
    last = quotes.groupby("contract").date.max()
    contracts = sorted(c for c in last.index if int(c[-2:]) in (3,6,9,12))
    rows = []
    for near, far in zip(contracts, contracts[1:]):
        if (int(far[2:4])*12+int(far[-2:]))-(int(near[2:4])*12+int(near[-2:])) != 3:
            continue
        expiry = last[near]
        # Only completed near contracts have an observed expiry, never infer it from truncated tail.
        if expiry >= calendar[-1] or expiry.month != int(near[-2:]) or not 15 <= expiry.day <= 25:
            continue
        a = quotes.loc[quotes.contract.eq(near)].set_index("date")
        b = quotes.loc[quotes.contract.eq(far)].set_index("date")
        joined = a[["close","settle","volume","open_interest"]].join(
            b[["close","settle","volume","open_interest"]], lsuffix="_near", rsuffix="_far", how="inner")
        t = calendar.get_indexer([expiry])[0]-calendar.get_indexer(joined.index)
        joined["td_to_expiry"] = t
        joined = joined.loc[joined.td_to_expiry.between(0,60)].copy()
        valid = (joined[["close_near","close_far","settle_near","settle_far","volume_near","volume_far","open_interest_near","open_interest_far"]]>0).all(axis=1)
        joined = joined.loc[valid]
        joined["product"], joined["near"], joined["far"] = product, near, far
        joined["near_expiry"] = expiry
        joined["quarter_month"] = expiry.month
        joined["year"] = expiry.year
        joined["points"] = joined.close_near-joined.close_far
        joined["ratio"] = joined.points/joined.close_near
        joined["settle_points"] = joined.settle_near-joined.settle_far
        joined["settle_ratio"] = joined.settle_points/joined.settle_near
        rows.append(joined.reset_index())
    return pd.concat(rows, ignore_index=True)


def paired_changes(panel):
    out = []
    for (product, near, far), g in panel.groupby(["product","near","far"]):
        node = g.set_index("td_to_expiry")
        for start, end in [(60,1),(40,1),(20,1),(10,1),(5,1),(3,1),(1,0)]:
            if start not in node.index or end not in node.index:
                continue
            a,b = node.loc[start],node.loc[end]
            out.append(dict(product=product,near=near,far=far,year=int(b.year),
                            quarter_month=int(b.quarter_month),start_td=start,end_td=end,
                            start_date=a.date,end_date=b.date,
                            delta_points=b.points-a.points,delta_ratio=b.ratio-a.ratio,
                            delta_settle_ratio=b.settle_ratio-a.settle_ratio))
    return pd.DataFrame(out)


def main():
    if OUT.exists():
        raise FileExistsError(OUT)
    panels, provenance = [], {}
    for product, path in SOURCES.items():
        q = pd.read_csv(path, parse_dates=["date"])
        provenance[product] = dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                   rows=len(q),start=str(q.date.min().date()),end=str(q.date.max().date()))
        panels.append(build_panel(product,q))
    panel = pd.concat(panels, ignore_index=True)
    changes = paired_changes(panel)
    OUT.mkdir(parents=True)
    panel.to_csv(OUT/"daily_pairs.csv.gz",index=False)
    changes.to_csv(OUT/"paired_changes.csv",index=False)
    nodes=panel.loc[panel.td_to_expiry.isin(NODES)]
    nodes.groupby(["product","td_to_expiry"]).agg(n=("near","size"),
        mean_points=("points","mean"),median_points=("points","median"),
        mean_ratio=("ratio","mean"),median_ratio=("ratio","median"),
        positive_share=("points",lambda s: float(s.gt(0).mean()))).to_csv(OUT/"expiry_profile.csv")
    summaries=[]
    for grouping in [["product","start_td","end_td"],
                     ["product","quarter_month","start_td","end_td"],
                     ["product","year","start_td","end_td"]]:
        for key,g in changes.groupby(grouping):
            summaries.append(dict(zip(grouping,key)) | dict(
                grouping="_".join(grouping),n=len(g),mean_delta_points=g.delta_points.mean(),
                median_delta_points=g.delta_points.median(),mean_delta_ratio=g.delta_ratio.mean(),
                median_delta_ratio=g.delta_ratio.median(),widen_share=g.delta_ratio.gt(0).mean(),
                mean_settle_delta_ratio=g.delta_settle_ratio.mean()))
    summary=pd.DataFrame(summaries)
    summary.to_csv(OUT/"change_summary.csv",index=False)
    # Earlier/later halves by independent expiry cycle, not by overlapping daily rows.
    split=[]
    for product,g in changes.groupby("product"):
        years=sorted(panel.loc[panel["product"].eq(product),"near_expiry"].unique())
        cutoff=pd.Timestamp(years[len(years)//2])
        for label,h in [("earlier",g.loc[pd.to_datetime(g.end_date)<cutoff]),("later",g.loc[pd.to_datetime(g.end_date)>=cutoff])]:
            for (a,b),j in h.groupby(["start_td","end_td"]):
                split.append(dict(product=product,half=label,cutoff=str(cutoff.date()),start_td=a,end_td=b,
                                  n=len(j),mean_delta_ratio=j.delta_ratio.mean(),widen_share=j.delta_ratio.gt(0).mean()))
    pd.DataFrame(split).to_csv(OUT/"split_validation.csv",index=False)
    audit=dict(source=provenance,sign="near_minus_far",costs="descriptive_spread_not_trading_returns",
               adjustment="raw_futures_no_backadjustment",complete_cycles=panel.groupby("product").near.nunique().to_dict(),
               no_future_prices_in_live_policy=True,missing_nodes="excluded_pairwise_never_filled")
    (OUT/"audit.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print(summary.loc[summary.grouping.eq("product_start_td_end_td")].to_string(index=False))


if __name__ == "__main__":
    main()
