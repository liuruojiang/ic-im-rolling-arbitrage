from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import research_im_put102_mom_floor_quantity_v1 as q

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260918_ic_im_im_v1_3_r7_mom120_put102_no_grid_no_call_im_core_and_momentum_put_mom120_floor_qty_no_grid_call"
SPEC = ROOT / "docs" / "im_put102_floor_no_grid_call_v1_spec.md"

def zero_grid(path: Path) -> pd.DataFrame:
    g = pd.read_csv(path, parse_dates=["date"])
    for col in g.columns:
        if col.startswith("overlay_") or col == "grid_carry":
            g[col] = 0.0
    return g

def main() -> None:
    if any((RUN / x).exists() for x in ("scan_summary.csv", "window_metrics.csv", "daily_outputs")):
        raise RuntimeError("outputs already exist")
    run = q.run
    state = pd.read_csv(run.BASE / "valuation_state_through_last_required_eval.csv.gz", parse_dates=["date"]).set_index("date")
    market = pd.read_csv(run.BASE / "model_market.csv.gz", parse_dates=["date"])
    upstream = pd.read_csv(run.BASE / "real_upstream.csv.gz", parse_dates=["date"])
    active = pd.read_csv(run.BASE / "real_active.csv.gz", parse_dates=["date"])
    raw = pd.read_csv(run.BASE / "real_options.csv.gz", parse_dates=["date", "contract_month", "rule_expiry", "actual_expiry"])
    options = run.engine.with_execution_prices(raw)
    rows=[]; all_daily=[]; costs=[]
    for scope in ("model", "real"):
        b = pd.read_csv(run.JOINT / f"{scope}_baseline_base.csv.gz", parse_dates=["date"])
        g = zero_grid(run.JOINT / f"{scope}_baseline_grid.csv.gz")
        templates={s: pd.read_csv(run.FULL / f"{scope}_dual_{s}_schedule.csv.gz", parse_dates=["eval_date", "execution_date"]) for s in ("core","mom")}
        for floor in q.FLOORS:
            legs=[]; trades=[]
            for sleeve in ("core","mom"):
                s=q.schedule(templates[sleeve],state,b.momentum_weight,scope,sleeve,floor)
                label=f"{scope}_nogridcall_floor{floor}_{sleeve}"
                if scope == "model":
                    p,t,_=run.engine.run_model_monthly_close(market,s,"3m",q.TARGET,label,reset_dates=run.engine.monthly_dates(b.date)); norm=(.5 if sleeve=="core" else .25)/4
                else:
                    p,t,_=run.engine.run_real_monthly_close(upstream,options,active,s,"3m",q.TARGET,label,reset_dates=run.engine.monthly_dates(b.date)); norm=.25/4
                if p.attrs.get("pending_end"): raise RuntimeError(label+" pending end")
                p[run.first.FIELDS]*=norm; legs.append(p); trades.append(t)
            put=sum(p[run.first.FIELDS] for p in legs); put["date"]=b.date
            d=run.comp.compose(b,put,g,None); d["scope"]=scope; d["candidate"]=f"{scope}_floor{floor}"; all_daily.append(d)
            rows.extend(q.metric_rows(d,f"{scope}_floor{floor}",scope))
            costs.append(dict(scope=scope,candidate=f"{scope}_floor{floor}",mom120_floor_qty=floor,mean_put_capital=float(d.put_mark_fraction.mean()),put_cost_total=float(d.put_cost_rate.sum()),min_cash=float(d.cash_weight.min()),trade_events=sum(len(x) for x in trades),max_dd=float(d.drawdown.min())))
    summary=pd.DataFrame(rows); wide=summary.pivot(index=["scope","candidate","mom120_floor_qty"],columns="segment",values=["ann_return","ann_vol","sharpe_repo","max_dd"]).reset_index(); wide.columns=["_".join(x).rstrip("_") if isinstance(x,tuple) else x for x in wide.columns]
    RUN.joinpath("daily_outputs").mkdir(); pd.concat(all_daily,ignore_index=True).to_csv(RUN/"daily_outputs"/"daily_candidates.csv.gz",index=False,compression="gzip"); summary.to_csv(RUN/"scan_summary.csv",index=False);wide.to_csv(RUN/"window_metrics.csv",index=False);pd.DataFrame(costs).to_csv(RUN/"protection_costs.csv",index=False)
    unavailable={f"real_floor{x}":{s:"Actual IM/MO begins 2022-07-22." for s in ("last_10y","last_5y")} for x in q.FLOORS}
    meta=json.loads((RUN/"scan_meta.json").read_text(encoding="utf-8"));meta.update(scan_type="one_parameter_sweep_no_grid_no_call",baseline={"candidate":"model_floor3"},candidate_grid=[{"floor":x,"target":1.02} for x in q.FLOORS],data_snapshot={"model":["2015-04-16",str(run.END.date())],"real":["2022-07-22",str(run.END.date())]},cost_model={"grid":"off","call":"off","futures_buffer":.3,"cash_annual":.03,"execution":"T signal/T+1 close","fees":"inherited Put/futures"},unavailable_segments=unavailable,decision="",stability_label="",warnings=["core plus momentum IM retained","no grid or Call","real zero-volume uses official settlement estimate"]);(RUN/"scan_meta.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    real=wide[wide.scope.eq("real")][["candidate","ann_return_full","sharpe_repo_full","max_dd_full","ann_return_last_3y","max_dd_last_3y","ann_return_last_1y","max_dd_last_1y"]].to_string(index=False)
    (RUN/"record.md").write_text(f"""# IM 102% Put：无网格/Call数量扫描\n\n## Run Metadata\n\n- 研究用途；未修改正式信号。\n\n## Research Question\n\n- 保留核心和动量IM/Put，关闭网格与Call，扫描1/2/3张负动量下限。\n\n## Implementation Anchor\n\n- 复用2026-09-08完整组合的期货和Put执行器；只将网格与Call损益/成本/保证金置零。\n\n## Data Snapshot\n\n- 模型2015-04-16至{run.END.date()}；真实MO 2022-07-22起。\n\n## Cost and Execution Assumptions\n\n- 30%期货缓冲、3%现金、T信号/T+1收盘及原Put费用。\n\n## Commands\n\n- `python research_im_put102_floor_no_grid_call_v1.py`\n\n## Output Files\n\n- `scan_summary.csv`, `window_metrics.csv`, `protection_costs.csv`。\n\n## Full-Sample Results\n\n```text\n{real}\n```\n\n## Window Results\n\n- 完整结果见CSV。\n\n## Stability Classification\n\n- Pending.\n\n## Decision\n\n- Decision: `pending_research_judgment`.\n\n## User-Facing Summary\n\n- 网格与Call均为0，动量IM保留。\n""",encoding="utf-8")
    with (RUN/"command_log.txt").open("a",encoding="utf-8") as f:f.write("python research_im_put102_floor_no_grid_call_v1.py\n")
    print(wide.to_json(orient="records",force_ascii=False,indent=2))
if __name__=="__main__":main()
