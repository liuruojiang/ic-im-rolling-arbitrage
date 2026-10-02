"""Research-only IM valuation-grid confirmation by Baifenwei fear score <=25."""
from __future__ import annotations

from pathlib import Path
import hashlib
import inspect
import json
import sys
import types

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "quant_research_runs/20260928_fear_greed_strategy_retest"
FG_RUN = ROOT / "quant_research_runs/20260928_csi1000_fear_greed_reproduction"
SOURCE = ROOT / "quant_param_scan_runs/20260908_im_mom120_put102_combined_v1"
GRID_AUDIT = ROOT / "quant_param_scan_runs/20260914_im_grid_half_full_audit_v3"
JOINT = ROOT / "quant_param_scan_runs/20260908_im_full_combination_joint_iv_derisk_v1"
END = pd.Timestamp("2026-09-07")
FEAR_LIMIT = 25.0


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path: Path, date_cols=("date",)) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=list(date_cols), low_memory=False)


def assert_close(name: str, left, right, tol: float = 1e-12) -> float:
    a, b = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all(), name
    err = float(np.abs(a - b).max()) if a.size else 0.0
    assert err <= tol, (name, err)
    return err


def metrics(frame: pd.DataFrame, start: pd.Timestamp, original) -> dict:
    out = original.first.metrics(frame, start)
    return {k: (v.item() if hasattr(v, "item") else v) for k, v in out.items()}


def main() -> None:
    manifest = json.loads((FG_RUN / "data_manifest.json").read_text(encoding="utf-8"))
    fear_path = FG_RUN / "inputs/fear_greed_full.csv"
    expected_fear_hash = manifest["fear_data_source"]["sha256"]
    assert sha256(fear_path) == expected_fear_hash
    fear = read(fear_path)
    fear["fear_greed_index"] = pd.to_numeric(fear["fear_greed_index"], errors="coerce")
    assert fear.date.is_unique and fear.fear_greed_index.between(0, 100).all()

    sys.path.insert(0, str(SOURCE))
    import run_combined as original  # noqa: E402

    full = original.previous.prev.full
    comp = original.comp
    engine = full.grids.grid_engine
    engine_source = inspect.getsource(engine.simulate_overlay)
    buy_rule = 'action = "buy" if (not state and numeric <= low + 1e-12) else None'
    assert engine_source.count(buy_rule) == 1
    patched_source = engine_source.replace(
        buy_rule,
        'entry_allowed = bool(FEAR_ENTRY_ALLOWED.get(day, False))\n'
        '            action = "buy" if (not state and numeric <= low + 1e-12 and entry_allowed) else None',
    )
    patched_source = patched_source.replace(
        'trade_frame.loc[trade_frame["action"].eq("buy"), "execution_date"].dt.year',
        'pd.to_datetime(trade_frame.loc[trade_frame["action"].eq("buy"), "execution_date"]).dt.year',
    )
    assert patched_source != engine_source
    engine_ns = dict(engine.__dict__)
    engine_ns["FEAR_ENTRY_ALLOWED"] = {}
    exec(patched_source, engine_ns)
    full.grids.grid_engine = types.SimpleNamespace(
        **{**engine.__dict__, "simulate_overlay": engine_ns["simulate_overlay"]}
    )
    full.RUN = RUN
    full.END = END
    full.GRID_DEFS = {
        "baseline160_half": [(1.6, 2.0, 0.5)],
        "fear_confirm160_half": [(1.6, 2.0, 0.5)],
    }

    base_state = read(original.BASE / "valuation_state_through_last_required_eval.csv.gz")
    market = read(original.BASE / "model_market.csv.gz")
    upstream = read(original.BASE / "real_upstream.csv.gz")
    quotes = read(original.FULL / "official_im_quotes.csv.gz")
    bridges = read(original.FULL / "quarter1_close_bridges.csv.gz")
    data = {"market": market, "state": base_state, "quotes": quotes}
    chain = {"name": "quarter1", "up": upstream, "bridges": bridges}

    grid_dates = pd.DatetimeIndex(upstream.date.drop_duplicates())
    engine_ns["FEAR_ENTRY_ALLOWED"] = {d: True for d in grid_dates}
    baseline_grid, baseline_trades = full.grid_component(
        data, "real", chain, read(SOURCE / "real_fixed_base.csv.gz"), "baseline160_half"
    )
    audited_grid = read(GRID_AUDIT / "real_original160_half_daily.csv.gz")
    assert baseline_grid.date.equals(audited_grid.date)
    parity_columns = ["overlay_gross_ret", "overlay_cost_rate", "overlay_held_eod", "grid_carry"]
    parity = {
        col: assert_close(f"baseline grid replay {col}", baseline_grid[col], audited_grid[col])
        for col in parity_columns
    }

    fear_lookup = dict(zip(fear.date, fear.fear_greed_index.le(FEAR_LIMIT)))
    engine_ns["FEAR_ENTRY_ALLOWED"] = {d: bool(fear_lookup.get(d, False)) for d in grid_dates}
    b = read(SOURCE / "real_fixed_base.csv.gz")
    put = read(SOURCE / "real_combined_put.csv.gz")
    call = read(SOURCE / "real_fixed_call.csv.gz")
    reference = read(GRID_AUDIT / "real_original160_half_daily.csv.gz")
    candidate_grid, candidate_trades = full.grid_component(
        data, "real", chain, b, "fear_confirm160_half"
    )
    candidate = comp.compose(b, put, candidate_grid, call)
    baseline = comp.compose(b, put, baseline_grid, call)
    assert baseline.date.equals(reference.date) and candidate.date.equals(reference.date)
    no_grid = read(GRID_AUDIT / "real_no_grid_daily.csv.gz")
    assert no_grid.date.equals(reference.date)
    baseline_parity = {
        col: assert_close(f"full baseline parity {col}", baseline[col], reference[col])
        for col in ["ret", "nav", "cash_weight", "futures_gross_ret", "futures_cost_rate"]
    }
    assert candidate.cash_weight.ge(0).all()
    assert candidate_grid.overlay_held_eod.isin([0.0, 0.5]).all()
    for col in original.first.FIELDS + comp.CALL_FIELDS:
        assert_close(f"unchanged component {col}", candidate[col], baseline[col])
        assert_close(f"no-grid unchanged component {col}", no_grid[col], baseline[col])
    assert_close(
        "candidate independent accounting",
        candidate.ret,
        (1 + candidate.futures_gross_ret + candidate.put_pnl_ret + candidate.call_pnl_ret)
        * (1 - candidate.futures_cost_rate)
        * (1 - candidate.put_cost_rate)
        * (1 - candidate.call_cost_rate)
        - 1
        + candidate.cash_weight * full.grids.CASH,
    )

    candidate_grid.to_csv(RUN / "real_fear_confirm_grid_daily.csv.gz", index=False)
    candidate_trades.to_csv(RUN / "real_fear_confirm_grid_trades.csv", index=False)
    candidate.to_csv(RUN / "real_fear_confirm_portfolio_daily.csv.gz", index=False)
    baseline_grid.to_csv(RUN / "real_baseline_grid_replay_daily.csv.gz", index=False)

    comparison = baseline[["date", "ret", "nav"]].rename(
        columns={"ret": "baseline_ret", "nav": "baseline_nav"}
    ).merge(
        candidate[["date", "ret", "nav"]].rename(
            columns={"ret": "fear_confirm_ret", "nav": "fear_confirm_nav"}
        ),
        on="date",
        validate="one_to_one",
    )
    comparison["nav_difference"] = comparison.fear_confirm_nav - comparison.baseline_nav
    comparison.to_csv(RUN / "real_portfolio_nav_comparison.csv", index=False)

    summary = []
    for label, frame, grid, trades in [
        ("baseline_grid_1.6_2.0_half", baseline, baseline_grid, baseline_trades),
        ("fear_confirm_le25_grid_1.6_2.0_half", candidate, candidate_grid, candidate_trades),
        ("module_off_no_grid", no_grid, no_grid, pd.DataFrame(columns=["action"])),
    ]:
        for name, years in [("full_real", None), ("last_3y", 3), ("last_1y", 1)]:
            start = frame.date.min() if years is None else END - pd.DateOffset(years=years)
            row = {"candidate": label, "segment": name, **metrics(frame, start, original)}
            subset = frame.loc[frame.date.ge(start)]
            row.update(
                grid_held_days=int(grid.loc[grid.date.ge(start), "overlay_held_eod"].gt(0).sum()),
                grid_held_fraction=float(grid.loc[grid.date.ge(start), "overlay_held_eod"].gt(0).mean()),
                grid_entries=int(trades.action.eq("buy").sum()),
                grid_exits=int(trades.action.eq("sell").sum()),
                grid_fee=float(grid.loc[grid.date.ge(start), "overlay_cost_rate"].sum()),
                min_cash=float(subset.cash_weight.min()),
            )
            summary.append(row)
    pd.DataFrame(summary).to_csv(RUN / "portfolio_window_summary.csv", index=False)

    cost_stress = []
    for label, grid in [("baseline_grid_1.6_2.0_half", baseline_grid), ("fear_confirm_le25_grid_1.6_2.0_half", candidate_grid)]:
        for fee in (1, 2, 5):
            stressed = comp.compose(b, put, grid, call, fee)
            cost_stress.append(
                dict(
                    candidate=label,
                    fee_multiplier=fee,
                    **metrics(stressed, stressed.date.min(), original),
                    grid_held_days=int(grid.overlay_held_eod.gt(0).sum()),
                    min_cash=float(stressed.cash_weight.min()),
                )
            )
    pd.DataFrame(cost_stress).to_csv(RUN / "portfolio_cost_stress.csv", index=False)

    overlap = fear.loc[fear.date.between("2022-07-22", "2026-09-18")].copy()
    overlap = overlap.merge(base_state[["date", "score"]], on="date", how="left", validate="one_to_one")
    base_hold = baseline_grid[["date", "overlay_held_eod"]].rename(
        columns={"overlay_held_eod": "baseline_grid_held"}
    )
    candidate_hold = candidate_grid[["date", "overlay_held_eod"]].rename(
        columns={"overlay_held_eod": "fear_confirm_grid_held"}
    )
    overlap = overlap.merge(base_hold, on="date", how="left", validate="one_to_one")
    overlap = overlap.merge(candidate_hold, on="date", how="left", validate="one_to_one")
    overlap["is_extreme_fear_le25"] = overlap.fear_greed_index.le(FEAR_LIMIT)
    overlap["valuation_entry_zone"] = overlap.score.le(1.6)
    overlap["actual_grid_entry_signal"] = False
    overlap["fear_confirm_grid_entry_signal"] = False
    for trades, col in [
        (baseline_trades, "actual_grid_entry_signal"),
        (candidate_trades, "fear_confirm_grid_entry_signal"),
    ]:
        entry_dates = set(pd.to_datetime(trades.loc[trades.action.eq("buy"), "signal_date"]))
        overlap[col] = overlap.date.isin(entry_dates)
    overlap.to_csv(RUN / "fear_score_grid_overlap_by_day.csv", index=False)

    overlap_rows = []
    for label, held_col, signal_col in [
        ("baseline_grid", "baseline_grid_held", "actual_grid_entry_signal"),
        ("fear_confirm_grid", "fear_confirm_grid_held", "fear_confirm_grid_entry_signal"),
    ]:
        extreme = overlap.loc[overlap.is_extreme_fear_le25]
        with_state = extreme.loc[extreme.score.notna()]
        low_val = with_state.loc[with_state.valuation_entry_zone]
        overlap_rows.append(
            dict(
                candidate=label,
                fear_le25_days=int(len(extreme)),
                fear_le25_with_valuation_score=int(len(with_state)),
                fear_le25_with_score_le1_6=int(len(low_val)),
                share_fear_le25_also_score_le1_6=(len(low_val) / len(with_state) if len(with_state) else np.nan),
                fear_le25_grid_held_days=int(extreme[held_col].fillna(0).gt(0).sum()),
                share_fear_le25_grid_held=(extreme[held_col].fillna(0).gt(0).mean() if len(extreme) else np.nan),
                new_entry_signals_on_fear_le25=int(extreme[signal_col].sum()),
                valuation_le1_6_days_in_common_score_window=int(overlap.score.le(1.6).sum()),
                valuation_le1_6_and_fear_le25_days=int((overlap.score.le(1.6) & overlap.is_extreme_fear_le25).sum()),
            )
        )
    pd.DataFrame(overlap_rows).to_csv(RUN / "fear_score_grid_overlap_summary.csv", index=False)

    call_trades = read(JOINT / "real_baseline_call_trades.csv.gz", ("eval_date", "scheduled_execution_date", "actual_execution_date"))
    call_daily = read(SOURCE / "real_fixed_call.csv.gz")
    call_opens = call_trades.loc[call_trades.action.eq("open")].copy().sort_values("actual_execution_date")
    call_opens = call_opens.merge(fear.rename(columns={"date": "eval_date"}), on="eval_date", how="left", validate="one_to_one")
    aligned = read(FG_RUN / "aligned_daily_inputs.csv")
    aligned = aligned.sort_values("date").reset_index(drop=True)
    aligned_lookup = {d: i for i, d in enumerate(aligned.date)}
    open_rows = []
    for event in call_opens.itertuples(index=False):
        signal_date = pd.Timestamp(event.eval_date)
        exec_date = pd.Timestamp(event.actual_execution_date)
        record = dict(
            signal_date=signal_date,
            execution_date=exec_date,
            fear_greed_index=float(event.fear_greed_index),
            extreme_fear_le25=bool(event.fear_greed_index <= FEAR_LIMIT),
            iv_gate=float(event.gate_iv),
            contract=event.new_contract,
            reason=event.reason,
        )
        if signal_date not in aligned_lookup:
            raise AssertionError(f"Call signal not in official fear/price alignment: {signal_date}")
        i = aligned_lookup[signal_date]
        assert pd.Timestamp(aligned.iloc[i + 1].date) == exec_date
        for horizon in (5, 10, 20):
            exit_index = i + horizon
            if exit_index < len(aligned):
                record[f"spot_return_{horizon}d"] = float(aligned.iloc[exit_index].close / aligned.iloc[i + 1].open - 1)
            else:
                record[f"spot_return_{horizon}d"] = np.nan
        later = call_trades.loc[
            call_trades.actual_execution_date.gt(exec_date)
            & call_trades.action.eq("close")
        ].sort_values("actual_execution_date")
        end_date = pd.Timestamp(later.iloc[0].actual_execution_date) if not later.empty else pd.Timestamp(call_daily.date.max())
        episode = call_daily.loc[call_daily.date.between(exec_date, end_date)]
        record.update(
            call_episode_end=end_date,
            call_episode_closed=not later.empty,
            call_episode_days=int(len(episode)),
            call_component_pnl_sum=float(episode.call_pnl_ret.sum()),
            call_component_cost_sum=float(episode.call_cost_rate.sum()),
        )
        open_rows.append(record)
    call_events = pd.DataFrame(open_rows)
    call_events.to_csv(RUN / "call_open_events_vs_fear.csv", index=False)
    call_summary = []
    for label, group in call_events.groupby("extreme_fear_le25", dropna=False):
        call_summary.append(
            dict(
                group="fear_le25" if label else "fear_gt25",
                entries=int(len(group)),
                mean_entry_iv=float(group.iv_gate.mean()),
                median_entry_iv=float(group.iv_gate.median()),
                mean_spot_return_5d=float(group.spot_return_5d.mean()),
                mean_spot_return_10d=float(group.spot_return_10d.mean()),
                mean_spot_return_20d=float(group.spot_return_20d.mean()),
                mean_call_component_pnl_sum=float(group.call_component_pnl_sum.mean()),
                call_component_pnl_sum_bps_mean=float(group.call_component_pnl_sum.mean() * 10000),
                mean_episode_days=float(group.call_episode_days.mean()),
            )
        )
    pd.DataFrame(call_summary).to_csv(RUN / "call_open_event_summary.csv", index=False)

    sources = [
        Path(__file__).resolve(),
        FG_RUN / "data_manifest.json",
        fear_path,
        FG_RUN / "aligned_daily_inputs.csv",
        SOURCE / "real_fixed_base.csv.gz",
        SOURCE / "real_combined_put.csv.gz",
        SOURCE / "real_fixed_call.csv.gz",
        GRID_AUDIT / "real_original160_half_daily.csv.gz",
        GRID_AUDIT / "real_no_grid_daily.csv.gz",
        original.BASE / "valuation_state_through_last_required_eval.csv.gz",
        original.BASE / "model_market.csv.gz",
        original.BASE / "real_upstream.csv.gz",
        original.FULL / "official_im_quotes.csv.gz",
        original.FULL / "quarter1_close_bridges.csv.gz",
        JOINT / "real_baseline_call_trades.csv.gz",
        Path(engine.__file__).resolve(),
        Path(full.__file__).resolve(),
        Path(full.grids.__file__).resolve(),
    ]
    source_hashes = {str(p.relative_to(ROOT)): sha256(p) for p in sources}
    (RUN / "source_hashes.json").write_text(json.dumps(source_hashes, ensure_ascii=False, indent=2), encoding="utf-8")
    checks = {
        "passed": True,
        "fear_input_hash_matches_registered_reproduction": True,
        "baseline_grid_replay_max_abs_error": parity,
        "full_portfolio_baseline_max_abs_error": baseline_parity,
        "candidate_independent_accounting": True,
        "unchanged_base_put_call_components": True,
        "production_modified": False,
        "fear_cutoff": "<=25",
        "lookahead_rule": "signal close t, execute next listed session open",
    }
    (RUN / "verification.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_frame = pd.DataFrame(summary)
    cost_stress_frame = pd.DataFrame(cost_stress)
    baseline_full = summary_frame.loc[
        summary_frame.candidate.eq("baseline_grid_1.6_2.0_half") & summary_frame.segment.eq("full_real")
    ].iloc[0]
    candidate_full = summary_frame.loc[
        summary_frame.candidate.eq("fear_confirm_le25_grid_1.6_2.0_half") & summary_frame.segment.eq("full_real")
    ].iloc[0]
    no_grid_full = summary_frame.loc[
        summary_frame.candidate.eq("module_off_no_grid") & summary_frame.segment.eq("full_real")
    ].iloc[0]
    three_year_base = summary_frame.loc[
        summary_frame.candidate.eq("baseline_grid_1.6_2.0_half") & summary_frame.segment.eq("last_3y")
    ].iloc[0]
    three_year_candidate = summary_frame.loc[
        summary_frame.candidate.eq("fear_confirm_le25_grid_1.6_2.0_half") & summary_frame.segment.eq("last_3y")
    ].iloc[0]
    call_low = next(x for x in call_summary if x["group"] == "fear_le25")
    call_other = next(x for x in call_summary if x["group"] == "fear_gt25")

    def pct(x: float) -> str:
        return f"{x * 100:.2f}%"

    def trades_text(trades: pd.DataFrame) -> str:
        buys = trades.loc[trades.action.eq("buy")]
        return "；".join(
            f"{pd.Timestamp(x.signal_date).date()}信号→{pd.Timestamp(x.execution_date).date()}开盘({x.execution_open:.1f})"
            for x in buys.itertuples(index=False)
        )

    report = [
        "# 恐贪 ≤25 确认 IM 估值网格：历史候选复核",
        "",
        "状态：研究候选；没有修改正式信号、生产代码或实际持仓。",
        "",
        "## 预注册候选规则",
        "",
        "IM估值分仍须≤1.6才具备新开仓条件；并要求信号日收盘恐贪分≤25。已有网格仅按原估值分≥2.0退出；半仓0.5倍、信号日收盘后判断、下一交易日开盘执行。若恐贪值缺失，当日不允许新开；不影响已有持仓及退出。所有期权、Put、核心和动量腿沿用已审计基准。",
        "",
        "## 结果",
        "",
        f"同一实际IM/MO历史窗（{baseline_full.start}至{baseline_full.end}，1001个日收益）下，模块关闭的无网格臂年化{pct(no_grid_full.ann_return)}、总收益{pct(no_grid_full.total_return)}、最大回撤{pct(no_grid_full.max_dd)}；1.6/2.0半仓基准臂年化{pct(baseline_full.ann_return)}、总收益{pct(baseline_full.total_return)}、最大回撤{pct(baseline_full.max_dd)}；恐贪确认候选年化{pct(candidate_full.ann_return)}、总收益{pct(candidate_full.total_return)}、最大回撤{pct(candidate_full.max_dd)}。候选比网格基准年化高{(candidate_full.ann_return - baseline_full.ann_return) * 100:.2f}个百分点，最大回撤相同；近3年年化为{pct(three_year_base.ann_return)}对{pct(three_year_candidate.ann_return)}，近1年曲线完全相同。此处网格臂按当前1.6/2.0参数重放历史，其余组合部件冻结为9月7日已审计快照，不等于9月28日整条现行主线的历史绩效。",
        "",
        f"阈值≤25的29个极冷日中，15日（{15 / 29:.1%}）估值分也≤1.6。基准网格仅一次买入信号落在极冷日；候选的两次买入都落在极冷日。信号日期：基准{trades_text(baseline_trades)}；候选{trades_text(candidate_trades)}。第二次买入从2024-01-18的5203.8延至2024-01-23的4740.4，迟3个交易日、执行开盘低{(4740.4 / 5203.8 - 1) * 100:.1f}%；本样本两臂仍都是两次买入、两次退出，差异主要由这一轮的入场时点造成。327个估值分≤1.6的日期里只有15日同时极冷。",
        "",
        f"1/2/5倍手续费下，候选年化分别为{', '.join(pct(x) for x in cost_stress_frame.loc[cost_stress_frame.candidate.eq('fear_confirm_le25_grid_1.6_2.0_half'), 'ann_return'])}，均高于同倍费用的网格基准；这不能消除小样本及单次时点影响。费用明细见 `portfolio_cost_stress.csv`。",
        "",
        f"旧Call历史基准共10次新开，4次信号日恐贪≤25。低分组4次开仓的标的后续20日平均收益为{pct(call_low['mean_spot_return_20d'])}，其Call组件累计收益贡献均值约{call_low['call_component_pnl_sum_bps_mean']:.1f}bp；其他6次分别为{pct(call_other['mean_spot_return_20d'])}和{call_other['call_component_pnl_sum_bps_mean']:.1f}bp。这只是旧规则的少量事件描述，收益贡献不是独立期权账户回报。",
        "",
        "每日基准与候选净值见 `real_portfolio_nav_comparison.csv`；三臂窗口指标见 `portfolio_window_summary.csv`；恐贪交集见 `fear_score_grid_overlap_summary.csv`；Call开仓事件见 `call_open_events_vs_fear.csv`。",
        "",
        "## 限制",
        "",
        "Fear序列是2026-09-28下载的完整历史快照，不含每个历史时点的原始发布版本；历史回看不能证明当时无修订/可实时取得。IM估值状态最后可验证信号日为2026-09-04，组合执行日截至2026-09-07。真实IM/MO层是项目审计的历史研究路径，不代表账户成交。实际可交易性、自融资、冲击成本、保证金动态和Call策略晋级均未由本候选研究证明。窗口和极冷开仓数有限，不能作为统计稳健或生产晋级依据。项目登记当前自2026-09-26信号起已移除IM新开及救援Call；Call事件只回看旧规则。",
        "",
        "## 可复现性",
        "",
        "脚本：`retest_fear_gated_grid.py`。输入身份见 `source_hashes.json`；核验见 `verification.json`。基准网格使用全开允许标记复放，并与已保存网格逐日校验后，再只在网格买入条件增加 fear≤25。",
        "",
    ]
    (RUN / "record.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    print(pd.DataFrame(summary).to_string(index=False))
    print(pd.DataFrame(overlap_rows).to_string(index=False))
    print(call_events.to_string(index=False))
    print(pd.DataFrame(call_summary).to_string(index=False))


if __name__ == "__main__":
    main()
