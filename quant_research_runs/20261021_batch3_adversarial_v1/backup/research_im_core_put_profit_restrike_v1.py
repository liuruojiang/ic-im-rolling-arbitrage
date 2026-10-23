"""IM current core Put: realize full profit and re-strike at 2x/3x entry premium."""
from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

import research_imc_current_core_put_decay60_router_fresh_v1 as common
import research_imc_current_core_put_short95_earlyvaluation_v4 as v4

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "quant_param_scan_runs" / "20260916_ic_im_v1_3_corrected_im_current_core_put_im_core_put_profit_recycle_and_restrike_baseline_full_recycle_at_2x_3x_entry_premium"
SPEC = ROOT / "docs" / "im_core_put_profit_restrike_v1_spec.md"
MULTIPLES = (None, 2.0, 3.0)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status() -> str:
    return subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True).stdout.strip()


def patched_engines():
    engine = common.engine
    real_src = inspect.getsource(engine.run_real_monthly_close)
    real_src = real_src.replace(
        "def run_real_monthly_close(upstream,options,active_im,schedule,tenor,moneyness,label,*,reset_dates,max_delay=5,anchor=None,market=None):",
        "def run_real_profit_restrike(upstream,options,active_im,schedule,tenor,moneyness,label,*,reset_dates,max_delay=5,anchor=None,market=None,profit_multiple=None):",
    )
    real_src = real_src.replace(
        "rows=[];trades=[];lives=[];skip_exit_pending=False",
        "rows=[];trades=[];lives=[];skip_exit_pending=False;entry_premium=np.nan;pending_profit=False",
    )
    real_src = real_src.replace(
        "if old is not None and oldq is None:raise RuntimeError(f'Missing mark {old.contract} {day}')\n        selected=None;action='';buy=sell=0;points=0.;old_price=new_price=np.nan",
        "if old is not None and oldq is None:raise RuntimeError(f'Missing mark {old.contract} {day}')\n        profit_reset=bool(old is not None and profit_multiple is not None and pending_profit and day not in reset_dates)\n        pending_profit=False\n        selected=None;action='';buy=sell=0;points=0.;old_price=new_price=np.nan",
    )
    real_src = real_src.replace(
        "replace=active is not None and (reset_since is not None or active.actual_expiry<=day)",
        "replace=active is not None and (reset_since is not None or active.actual_expiry<=day or profit_reset)",
    )
    real_src = real_src.replace(
        "action='close_roll_monthly' if reset_since is not None else 'close_expiry_replace'",
        "action='close_roll_monthly' if reset_since is not None else ('close_profit_restrike' if profit_reset else 'close_expiry_replace')",
    )
    real_src = real_src.replace(
        "if buy and action in ('close_buy','close_roll_monthly','close_expiry_replace'):",
        "if buy and action in ('close_buy','close_roll_monthly','close_expiry_replace','close_profit_restrike'):",
    )
    real_src = real_src.replace(
        "active=legacy.RealPosition(str(selected['contract']),pd.Timestamp(selected['contract_month']),\n                pd.Timestamp(selected['actual_expiry']),target,float(selected['settle']),day)",
        "active=legacy.RealPosition(str(selected['contract']),pd.Timestamp(selected['contract_month']),\n                pd.Timestamp(selected['actual_expiry']),target,float(selected['settle']),day)\n            entry_premium=float(new_price)",
    )
    real_src = real_src.replace(
        "if active is None:skip_exit_pending=False",
        "if active is None:skip_exit_pending=False;entry_premium=np.nan;pending_profit=False\n        elif profit_multiple is not None and action!='close_profit_restrike' and np.isfinite(entry_premium) and active.prior_settle>=entry_premium*profit_multiple:pending_profit=True",
    )

    model_src = inspect.getsource(engine.run_model_monthly_close)
    model_src = model_src.replace(
        "def run_model_monthly_close(market, schedule, tenor, moneyness, label, *, reset_dates, anchor=None):",
        "def run_model_profit_restrike(market, schedule, tenor, moneyness, label, *, reset_dates, anchor=None, profit_multiple=None):",
    )
    model_src = model_src.replace(
        "active=None; target=0; evaluation=None; rows=[];trades=[];lives=[];skip_exit_pending=False;last_signal_target=0",
        "active=None; target=0; evaluation=None; rows=[];trades=[];lives=[];skip_exit_pending=False;last_signal_target=0;entry_premium=np.nan;pending_profit=False",
    )
    model_src = model_src.replace(
        "active.prior_mark=old_price\n        if active is not None and target==0:",
        "active.prior_mark=old_price\n        profit_reset=bool(active is not None and profit_multiple is not None and pending_profit and day not in reset_dates)\n        pending_profit=False\n        if active is not None and target==0:",
    )
    model_src = model_src.replace(
        "elif target>0 and (active is None or reset or expired):",
        "elif target>0 and (active is None or reset or expired or profit_reset):",
    )
    model_src = model_src.replace(
        "new_price=legacy.v6.option_price(active,r,'close');active.prior_mark=new_price",
        "new_price=legacy.v6.option_price(active,r,'close');active.prior_mark=new_price;entry_premium=new_price",
    )
    model_src = model_src.replace(
        "action='close_roll_monthly' if reset else ('close_expiry_replace' if expired else 'close_buy')",
        "action='close_roll_monthly' if reset else ('close_expiry_replace' if expired else ('close_profit_restrike' if profit_reset else 'close_buy'))",
    )
    model_src = model_src.replace(
        "if active is None:skip_exit_pending=False",
        "if active is None:skip_exit_pending=False;entry_premium=np.nan;pending_profit=False\n        elif profit_multiple is not None and action!='close_profit_restrike' and np.isfinite(entry_premium) and active.prior_mark>=entry_premium*profit_multiple:pending_profit=True",
    )
    real_ns = dict(vars(engine)); model_ns = dict(vars(engine))
    exec(compile(real_src, str(Path(__file__)), "exec"), real_ns)
    exec(compile(model_src, str(Path(__file__)), "exec"), model_ns)
    return real_ns["run_real_profit_restrike"], model_ns["run_model_profit_restrike"], real_src + "\n\n" + model_src


def layer(scope: str, real_engine, model_engine):
    router = common.router
    if scope == "real":
        base = pd.read_csv(router.BASE, parse_dates=["date"])
        raw = pd.read_csv(router.OPTIONS, parse_dates=["date"])
        raw["contract_month"] = pd.to_datetime("20" + raw.contract.str[2:6], format="%Y%m")
        options = common.prepare_options(raw, common.actual_expiry_map(raw, base))
        options = common.engine.with_execution_prices(options)
        market = None
    else:
        market, base, _, _, _, _ = common.model_source.build_inputs()
        options = None
    bare = pd.DataFrame({"date": base.date, "candidate": f"{scope}_bare_monthly_imc", "return_net": base.baseline_plus_cash_ret.astype(float)})
    bare["nav"] = (1 + bare.return_net).cumprod(); bare["state"] = "imc"; bare["action"] = ""
    schedule = v4.corrected_core_schedule(base.date, scope, pd.Series(True, index=pd.DatetimeIndex(base.date)))
    candidates = [bare]; trades_all = []; audit = {}
    for multiple in MULTIPLES:
        tag = "baseline" if multiple is None else f"profit{int(multiple)}x"
        label = f"{scope}_im_coreput_{tag}"
        if scope == "real":
            put, trades, lives = real_engine(base, options, base, schedule, "3m", 1.02, label, reset_dates=common.engine.monthly_dates(base.date), market=None, profit_multiple=multiple)
        else:
            put, trades, lives = model_engine(market, schedule, "3m", 1.02, label, reset_dates=common.engine.monthly_dates(base.date), profit_multiple=multiple)
        put = common.scale_put(put, scope)
        combined = common.apply_core_put(bare, put); combined["candidate"] = label
        candidates.append(combined)
        trades_all.append(trades.assign(candidate=label))
        audit[label] = {
            "trade_events": len(trades), "profit_restrikes": int(trades.action.eq("close_profit_restrike").sum()),
            "monthly_rolls": int(trades.action.eq("close_roll_monthly").sum()), "lives": len(lives),
        }
    return pd.concat(candidates, ignore_index=True), pd.concat(trades_all, ignore_index=True), audit


def main() -> None:
    meta_path = RUN / "scan_meta.json"; meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("phase") != "init": raise RuntimeError("Refusing to overwrite non-init run")
    real_engine, model_engine, executed = patched_engines()
    real_daily, real_trades, real_audit = layer("real", real_engine, model_engine)
    model_daily, model_trades, model_audit = layer("model", real_engine, model_engine)
    daily = pd.concat([real_daily, model_daily], ignore_index=True)
    summary, wide, unavailable = common.window_tables(daily)
    out = RUN / "daily_outputs"; out.mkdir(exist_ok=False)
    daily.to_csv(out / "daily.csv.gz", index=False, compression="gzip")
    pd.concat([real_trades, model_trades], ignore_index=True).to_csv(out / "trades.csv", index=False)
    summary.to_csv(RUN / "scan_summary.csv", index=False, encoding="utf-8-sig")
    wide.to_csv(RUN / "window_metrics.csv", index=False, encoding="utf-8-sig")
    (RUN / "executed_put_engines.py").write_text(executed, encoding="utf-8")
    full = summary[summary.segment.eq("full")]
    meta.update(
        scan_type="im_core_put_profit_restrike_scan",
        baseline={"candidate": "*_im_coreput_baseline"}, candidate_grid=[{"profit_multiple": x} for x in ("none", 2, 3)],
        data_snapshot={"real_start": str(real_daily.date.min().date()), "real_end": str(real_daily.date.max().date()), "model_start": str(model_daily.date.min().date()), "model_end": str(model_daily.date.max().date())},
        cost_model={"put_side_cost": "existing engine", "reserve": 0.30, "cash_annual": 0.03, "recycle": "same close sell old and buy new 3m 102% Put at current target quantity"},
        audit={"real": real_audit, "model": model_audit}, unavailable_segments=unavailable,
        outputs={**meta["outputs"], "daily": str(out / "daily.csv.gz"), "trades": str(out / "trades.csv"), "executed_put_engines": str(RUN / "executed_put_engines.py")},
        source_hashes={"script": sha(Path(__file__)), "spec": sha(SPEC), "early_valuation": sha(v4.EARLY)},
        warnings=["Component-level monthly IMC plus current core Put; not complete v1.3 attribution.", "Historical 102% and debounce are counterfactual replay.", "Model layer is theoretical/proxy and not executable history.", "No bid-ask, impact, dynamic margin, forced liquidation, tax, capacity, or integer sizing."],
        decision="research_only_pending_interpretation", stability_label="profit_restrike_pending_review", git_status_after=git_status(),
    )
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record = "# IM核心Put盈利兑现并重新定价\n\n## Data\n\n真实与理论分层；早期估值恢复。\n\n## Results\n\n" + full.to_markdown(index=False) + "\n\n## Audit\n\n```json\n" + json.dumps(meta["audit"], ensure_ascii=False, indent=2) + "\n```\n\n## Stability\n\n待解释。\n\n## Decision\n\nresearch_only_pending_interpretation\n"
    (RUN / "record.md").write_text(record, encoding="utf-8")
    with (RUN / "command_log.txt").open("a", encoding="utf-8") as handle: handle.write(f"cwd={ROOT}\npython -X utf8 {Path(__file__).name}\n")
    print(full.to_string(index=False)); print(json.dumps(meta["audit"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
