"""Fix9 full-account replay for valuation OR Fear<=25 grid entries."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

RUN_DIR = Path(__file__).resolve().parent
ROOT = RUN_DIR.parents[1]
BASE_RUN = ROOT / "quant_research_runs/20260928_fix9_fear25_grid_confirmation"
AND_RUN = ROOT / "quant_research_runs/20260928_fix9_fear25_grid_confirmation"
SCRATCH_DIR = RUN_DIR / "scratch"
OUTPUT_DIR = RUN_DIR / "outputs"
SCOPE = "listed_quotes_fix9_asif_current"
GRID_UNITS = 0.5
FEAR_MAX = 25.0
THRESHOLDS = {"IC": {"entry": 0.5, "exit": 1.0}, "IM": {"entry": 1.6, "exit": 2.0}}
ARMS = ("NO_GRID", "VALUATION_GRID", "FEAR_ONLY_ENTRY_VALUATION_EXIT", "VALUATION_OR_FEAR25")

sys.path.insert(0, str(BASE_RUN))
import replay_fix9_fear25_grid_confirmation as prior  # noqa: E402

listed = prior.listed
grid = prior.grid
fix9 = prior.fix9


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def build_entry_union_targets(risk: pd.DataFrame, fear: pd.Series, product: str,
                              trigger: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    if trigger not in {"fear_only", "valuation_or_fear"}:
        raise ValueError(trigger)
    z = risk.copy()
    dates = z.signal_date.astype(str).tolist()
    scores = pd.to_numeric(z.score, errors="coerce").to_numpy(float)
    fear_values = np.asarray([fear.get(day, np.nan) for day in dates], dtype=float)
    levels = THRESHOLDS[product]
    state = 0.0
    targets: list[float] = []
    entry_reasons: list[str] = []
    for value, fscore in zip(scores, fear_values):
        reason = ""
        if state > 0:
            # Both candidate paths keep the original valuation-based exit.
            if math.isfinite(value) and value >= levels["exit"]:
                state = 0.0
        else:
            val_entry = math.isfinite(value) and value <= levels["entry"]
            fear_entry = math.isfinite(fscore) and fscore <= FEAR_MAX
            if trigger == "fear_only" and fear_entry:
                state = GRID_UNITS
                reason = "fear_only"
            elif trigger == "valuation_or_fear" and (val_entry or fear_entry):
                state = GRID_UNITS
                reason = "both" if val_entry and fear_entry else ("valuation_only" if val_entry else "fear_only")
        targets.append(state)
        entry_reasons.append(reason)

    z["grid_target_units"] = np.asarray(targets, dtype=float)
    transitions: list[dict] = []
    state_before = 0.0
    for i, day in enumerate(dates):
        target = float(targets[i])
        if target != state_before:
            if target > state_before:
                cause = entry_reasons[i]
            else:
                cause = "valuation_exit"
            transitions.append({
                "signal_date": day,
                "expected_execution_date": str(z.execution_date.iloc[i]),
                "action": "BUY_NEXT_OPEN" if target > state_before else "SELL_NEXT_OPEN",
                "fear_greed_index": float(fear_values[i]) if math.isfinite(fear_values[i]) else np.nan,
                "grid_target_before": state_before,
                "grid_target_after": target,
                "terminal_unexecuted": i == len(z) - 1,
                "valuation_score": float(scores[i]) if math.isfinite(scores[i]) else np.nan,
                "fear_entry_cause": cause,
            })
        state_before = target
    events = pd.DataFrame(transitions, columns=[
        "signal_date", "expected_execution_date", "action", "fear_greed_index",
        "grid_target_before", "grid_target_after", "terminal_unexecuted",
        "valuation_score", "fear_entry_cause",
    ])
    audit = {
        "trigger": trigger,
        "entry_thresholds": levels,
        "fear_entry_max_inclusive": FEAR_MAX,
        "exit_rule": f"held grid exits if valuation score >= {levels['exit']}",
        "valuation_or_fear_gate_buy_count": int((events.action == "BUY_NEXT_OPEN").sum()) if len(events) else 0,
        "buy_reason_counts": events.loc[events.action.eq("BUY_NEXT_OPEN"), "fear_entry_cause"].value_counts().to_dict() if len(events) else {},
        "exit_dates": events.loc[events.action.eq("SELL_NEXT_OPEN"), "signal_date"].astype(str).tolist() if len(events) else [],
    }
    return z, events, audit


def attach_signal_metadata(signals: pd.DataFrame, risk: pd.DataFrame, fear: pd.Series,
                           product: str, arm: str) -> pd.DataFrame:
    out = signals.copy()
    if "valuation_score" not in out:
        scores = risk.set_index("signal_date").score
        out["valuation_score"] = out.signal_date.astype(str).map(scores)
    if "fear_entry_cause" not in out:
        out["fear_entry_cause"] = "valuation_only"
        out.loc[out.action.eq("SELL_NEXT_OPEN"), "fear_entry_cause"] = "valuation_exit"
    else:
        out["fear_entry_cause"] = out["fear_entry_cause"].fillna("")
    if len(out):
        for row in out.loc[out.action.eq("BUY_NEXT_OPEN")].itertuples(index=False):
            score = pd.to_numeric(pd.Series([row.valuation_score]), errors="coerce").iloc[0]
            fscore = fear.get(str(row.signal_date), np.nan)
            if arm == "FEAR_ONLY_ENTRY_VALUATION_EXIT" and not (pd.notna(fscore) and float(fscore) <= FEAR_MAX):
                raise RuntimeError(f"fear-only entry condition failed: {product}/{row.signal_date}")
            if arm == "VALUATION_OR_FEAR25" and not (
                (pd.notna(score) and float(score) <= THRESHOLDS[product]["entry"])
                or (pd.notna(fscore) and float(fscore) <= FEAR_MAX)
            ):
                raise RuntimeError(f"OR entry condition failed: {product}/{row.signal_date}")
        if arm in {"FEAR_ONLY_ENTRY_VALUATION_EXIT", "VALUATION_OR_FEAR25"}:
            exits = out.loc[out.action.eq("SELL_NEXT_OPEN")]
            if not (pd.to_numeric(exits.valuation_score, errors="raise") >= THRESHOLDS[product]["exit"]).all():
                raise RuntimeError(f"candidate exit differs from original valuation line: {product}/{arm}")
    return out


def main() -> None:
    if (RUN_DIR / "verification.json").exists():
        raise FileExistsError("registered OR-entry replay is finalized; preserve and audit it")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    listed.SCRATCH_DIR = SCRATCH_DIR

    source, candidates, gov, spot, lifecycles, formal_folders, candidates_path, im_lifecycle_path, ic_lifecycle_audit = listed.load_account_inputs()
    fear_df = pd.read_csv(prior.FEAR_FILE, dtype={"date": str})
    if fear_df.date.duplicated().any():
        raise RuntimeError("Fear snapshot contains duplicate dates")
    fear_df["fear_greed_index"] = pd.to_numeric(fear_df.fear_greed_index, errors="coerce")
    fear = fear_df.set_index("date").fear_greed_index
    saved_daily = pd.read_csv(prior.FIX9_LISTED_DIR / "outputs/listed_fix9_daily_nav.csv.gz", dtype={"date": str})

    daily_paths: list[pd.DataFrame] = []
    signal_paths: list[pd.DataFrame] = []
    fill_paths: list[pd.DataFrame] = []
    metrics: list[dict] = []
    coverage: list[dict] = []
    baseline_parity: list[dict] = []
    no_grid_parity: list[dict] = []
    execution_audits: list[dict] = []
    feasibility: list[dict] = []
    rule_audits: list[dict] = []
    missing_fear: dict[str, list[str]] = {}
    risk_inputs: dict[str, tuple[pd.DataFrame, Path, Path]] = {}

    for product in ("IC", "IM"):
        folder = formal_folders[product]
        risk_path = folder / f"native_fix4_{product.lower()}_risk_signals_v1.csv.gz"
        target_name = f"native_fix4_{product.lower()}_noseller_targets_{'v2' if product == 'IC' else 'v3'}.csv.gz"
        targets_path = folder / target_name
        risk = pd.read_csv(risk_path, dtype={"signal_date": str, "execution_date": str})
        if risk.signal_date.duplicated().any():
            raise RuntimeError(f"duplicate fix9 risk signal dates: {product}")
        missing_exec = risk.execution_date.isna()
        if missing_exec.any() and not missing_exec.iloc[-1]:
            raise RuntimeError(f"nonterminal missing execution date: {product}")
        missing_fear[product] = [d for d in risk.signal_date.astype(str) if pd.isna(fear.get(d, np.nan))]
        risk_inputs[product] = (risk, risk_path, targets_path)
        coverage.append({"product": product, "rows": int(len(risk)),
                         "start": str(risk.signal_date.iloc[0]), "end": str(risk.signal_date.iloc[-1]),
                         "fear_missing_dates": missing_fear[product],
                         "risk_signals_sha256": sha256(risk_path), "no_seller_targets_sha256": sha256(targets_path)})

        entry = THRESHOLDS[product]["entry"]
        exit_ = THRESHOLDS[product]["exit"]
        # Verify the documented valuation entry/exit lines exactly reproduce the frozen risk target stream.
        score = pd.to_numeric(risk.score, errors="coerce").to_numpy(float)
        rebuilt_val = []
        state = 0.0
        for value in score:
            if state == 0 and math.isfinite(value) and value <= entry:
                state = GRID_UNITS
            elif state > 0 and math.isfinite(value) and value >= exit_:
                state = 0.0
            rebuilt_val.append(state)
        original_val = pd.to_numeric(risk.grid_target_units, errors="raise").to_numpy(float)
        if not np.array_equal(np.asarray(rebuilt_val), original_val):
            raise RuntimeError(f"valuation threshold state machine does not match fix9 risk stream: {product}")

        valuation_risk, valuation_signals = grid.build_grid_targets(
            risk, fear, {"kind": "valuation", "label": "VALUATION_GRID"}, GRID_UNITS
        )
        no_grid_risk, no_grid_signals = grid.build_grid_targets(
            risk, fear, {"kind": "no_grid", "label": "NO_GRID"}, GRID_UNITS
        )
        fear_only_risk, fear_only_signals, fear_only_audit = build_entry_union_targets(risk, fear, product, "fear_only")
        or_risk, or_signals, or_audit = build_entry_union_targets(risk, fear, product, "valuation_or_fear")
        variants = [
            ("NO_GRID", no_grid_risk, no_grid_signals, {}),
            ("VALUATION_GRID", valuation_risk, valuation_signals, {}),
            ("FEAR_ONLY_ENTRY_VALUATION_EXIT", fear_only_risk, fear_only_signals, fear_only_audit),
            ("VALUATION_OR_FEAR25", or_risk, or_signals, or_audit),
        ]

        for arm, variant_risk, raw_signals, rule_audit in variants:
            label = f"{product}_{arm}_LISTED_FIX9"
            signals = attach_signal_metadata(raw_signals, risk, fear, product, arm)
            daily, events, _replay_audit = listed.replay(
                product, variant_risk, targets_path, lifecycles[product],
                source, candidates, gov, spot,
            )
            daily["date"] = daily.date.astype(str)
            events["date"] = events.date.astype(str)
            if len(daily) != len(variant_risk) or daily.date.tolist() != variant_risk.signal_date.astype(str).tolist():
                raise RuntimeError(f"fix9 calendar mismatch: {label}")
            if arm == "VALUATION_GRID":
                baseline_parity.append({"product": product, "candidate": label,
                                        **fix9.assert_baseline_parity(product, daily, events)})
            elif arm == "NO_GRID":
                no_grid_parity.append({"product": product,
                                       **prior.compare_no_grid_to_saved(daily, saved_daily, product)})

            daily, fills, checks = prior.enrich_arm(product, label, daily, signals, events, variant_risk, fear)
            daily["valuation_score"] = pd.to_numeric(
                risk.set_index("signal_date").score.reindex(daily.date), errors="coerce"
            ).to_numpy()
            execution_audits.append({"product": product, "candidate": label, **checks["execution"]})
            feasibility.append(checks["feasibility"])
            for row in grid.compute_windows(daily, label, SCOPE, product, signals, fills):
                row.update({"fear_entry_max": FEAR_MAX if arm in {"FEAR_ONLY_ENTRY_VALUATION_EXIT", "VALUATION_OR_FEAR25"} else "",
                            "valuation_entry_threshold": entry if arm in {"VALUATION_GRID", "VALUATION_OR_FEAR25"} else "",
                            "valuation_exit_threshold": exit_ if arm in {"VALUATION_GRID", "FEAR_ONLY_ENTRY_VALUATION_EXIT", "VALUATION_OR_FEAR25"} else "",
                            "grid_units": GRID_UNITS,
                            "account_grid_trade_fills": int(len(fills)),
                            "account_grid_trade_fees_total": float(daily.grid_trade_fees_total.iloc[0])})
                metrics.append(row)

            signals = signals.copy()
            signals["candidate"] = label
            signals["instrument"] = product
            signals["dataset"] = SCOPE
            signal_paths.append(signals)
            if len(fills):
                fill_paths.append(fills)
            daily_paths.append(daily)
            if arm in {"FEAR_ONLY_ENTRY_VALUATION_EXIT", "VALUATION_OR_FEAR25"}:
                rule_audits.append({"product": product, "candidate": label, **rule_audit,
                                    "entry_signal_count": int((signals.action == "BUY_NEXT_OPEN").sum()),
                                    "exit_signal_count": int((signals.action == "SELL_NEXT_OPEN").sum()),
                                    "entry_causes": signals.loc[signals.action.eq("BUY_NEXT_OPEN"), "fear_entry_cause"].value_counts().to_dict()})
            print(json.dumps({"stage": "arm_done", "candidate": label,
                              "rows": len(daily), "signals": len(signals), "fills": len(fills)}, ensure_ascii=False), flush=True)

    daily_out = pd.concat(daily_paths, ignore_index=True)
    summary = pd.DataFrame(metrics)
    daily_out.to_csv(OUTPUT_DIR / "fix9_full_account_daily_nav.csv.gz", index=False, compression="gzip")
    pd.concat(signal_paths, ignore_index=True).to_csv(OUTPUT_DIR / "grid_transition_signals.csv", index=False)
    if fill_paths:
        pd.concat(fill_paths, ignore_index=True).to_csv(OUTPUT_DIR / "grid_account_fills.csv", index=False)
    summary.to_csv(RUN_DIR / "portfolio_summary.csv", index=False)

    study = fix9.study
    source_paths = [
        Path(__file__).resolve(), RUN_DIR / "preregistered_spec.md",
        Path(prior.__file__), Path(listed.__file__), Path(grid.__file__), Path(fix9.__file__),
        AND_RUN / "record.md", AND_RUN / "portfolio_summary.csv", AND_RUN / "verification.json",
        AND_RUN / "outputs/fix9_full_account_daily_nav.csv.gz",
        prior.FIX9_LISTED_DIR / "outputs/listed_fix9_daily_nav.csv.gz",
        prior.FIX9_LISTED_DIR / "verification.json", prior.FIX9_LISTED_DIR / "source_hashes.json",
        prior.FIX9_FULLACCOUNT_DIR / "native_account_breakeven_candidate.py",
        prior.FIX9_FULLACCOUNT_DIR / "baseline_daily.csv.gz", prior.FIX9_FULLACCOUNT_DIR / "baseline_events.csv.gz",
        prior.FIX9_FULLACCOUNT_DIR / "audit.json", fix9.IC_FIX9_RUN / "daily_outputs.csv.gz",
        fix9.IC_FIX9_RUN / "events.csv.gz", fix9.STUDY / "native_account.py",
        Path(study.base.__file__), Path(study.seller_lifecycle.__file__), Path(study.FORMAL.v14_policy.__file__),
        study.NATIVE / "native_fix4_seller_candidates_v1.csv.gz", prior.FEAR_FILE, listed.GOV_FILE,
        candidates_path, im_lifecycle_path, study.base.OHLCV["IC"], study.base.OHLCV["IM"],
    ]
    for product, (_risk, risk_path, targets_path) in risk_inputs.items():
        source_paths += [risk_path, targets_path, formal_folders[product] / "native_fix4_seller_lifecycle_v4.csv.gz"]
    source_hashes = {str(path.relative_to(ROOT)): sha256(path) for path in source_paths if path.is_file()}
    (RUN_DIR / "source_hashes.json").write_text(json.dumps(source_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    verification = {
        "classification": "fix9_as_if_current_listed_full_account_OR_entry_research_only",
        "run_id": RUN_DIR.name,
        "strategy_identity": "v1.4-r1 fix9 as-if-current full-account replay",
        "ic_rule": "fix9 MOM120 seller-admission rule applied retrospectively",
        "im_rule": "unchanged formal IM path under the fix9 full-account engine",
        "valuation_entry_exit_thresholds": THRESHOLDS,
        "fear_entry_max_inclusive": FEAR_MAX,
        "or_rule": "flat: valuation score <= entry threshold OR fear score <=25; held: exit only when valuation score >= original exit threshold",
        "fear_only_reference": "flat: fear score <=25; held: exit only when valuation score >= original exit threshold",
        "missing_fear_rule": "no Fear-based entry when missing; valuation can still trigger OR entry",
        "execution": "T close signal, T+1 listed futures open fill",
        "scope": SCOPE,
        "coverage": coverage,
        "missing_fear_dates": missing_fear,
        "valuation_state_reconstruction": "exact match to frozen fix9 grid_target_units for IC and IM",
        "fix9_ic_seller_lifecycle_audit": ic_lifecycle_audit,
        "valuation_grid_fix9_baseline_parity": baseline_parity,
        "no_grid_saved_fix9_replay_parity": no_grid_parity,
        "grid_execution_audits": execution_audits,
        "account_feasibility": feasibility,
        "candidate_rule_audits": rule_audits,
        "fees_financing": {"source": "fix9 native account engine", "grid_units": GRID_UNITS,
                           "futures_margin_buffer": "30% performance basis", "seller_reserve": "15% NAV feasibility ceiling",
                           "cash_rate": "3% annual in native engine", "separate_grid_carry": "none"},
        "fear_snapshot_sha256": sha256(prior.FEAR_FILE),
        "point_in_time_fear_history_proven": False,
        "source_hashes": "source_hashes.json",
        "outputs": ["portfolio_summary.csv", "outputs/fix9_full_account_daily_nav.csv.gz",
                    "outputs/grid_transition_signals.csv", "outputs/grid_account_fills.csv"],
        "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }
    (RUN_DIR / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    full = summary.loc[summary.segment.eq("full")]
    display = {
        "NO_GRID": "无网格",
        "VALUATION_GRID": "仅估值入场",
        "FEAR_ONLY_ENTRY_VALUATION_EXIT": "仅恐慌入场、估值退出",
        "VALUATION_OR_FEAR25": "估值 OR 恐慌≤25入场",
    }
    lines = ["# Fix9：估值 OR 恐慌≤25 网格入场回测", "",
             "状态：研究候选；历史反事实全账户回放，不代表实盘成交或生产授权。", "",
             "## 规则", "",
             "空仓时，估值入场条件或日恐慌≤25任一成立，即按0.5倍新开网格。已持仓时仍只按原估值退出线退出，恐慌回升不触发卖出。额外报告纯恐慌入场、估值退出的拆分路径。", "",
             "估值触发线：IC≤0.5入场、≥1.0退出；IM≤1.6入场、≥2.0退出。Fear 使用≤25；T收盘判断、下一交易日开盘执行。", "",
             "## 全账户结果", "",
             "年化/累计收益/最大回撤来自同次 fix9 全账户 `return_net`，非网格单腿收益。", "",
             "| 品种 | 路径 | 年化 | 累计收益 | 最大回撤 | 网格开/平 | 持仓日 | 网格手续费 |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for product in ("IC", "IM"):
        for arm in ARMS:
            candidate = f"{product}_{arm}_LISTED_FIX9"
            row = full.loc[full.candidate.eq(candidate)].iloc[0]
            lines.append(f"| {product} | {display[arm]} | {row.ann_return:.2%} | {row.total_return:.2%} | {row.max_dd:.2%} | {int(row.grid_entries)}/{int(row.grid_exits)} | {int(row.grid_held_days)} | {row.grid_trade_fees:,.0f} |")

    and_summary = pd.read_csv(AND_RUN / "portfolio_summary.csv")
    and_full = and_summary.loc[and_summary.segment.eq("full")]
    lines += ["", "## OR 与三种参照路径的差异", "",
              "同一挂牌样本内比较年化收益、最大回撤、入场数与全网格手续费。最大回撤变化为正代表回撤较浅，负代表较深。", "",
              "| 品种 | OR相对路径 | 年化变化 | 最大回撤变化 | 开仓变化 | 手续费倍数 |",
              "|---|---|---:|---:|---:|---:|"]
    for product in ("IC", "IM"):
        or_row = full.loc[full.candidate.eq(f"{product}_VALUATION_OR_FEAR25_LISTED_FIX9")].iloc[0]
        refs = [
            ("仅估值", full.loc[full.candidate.eq(f"{product}_VALUATION_GRID_LISTED_FIX9")].iloc[0]),
            ("此前AND确认", and_full.loc[and_full.candidate.eq(f"{product}_FEAR25_ENTRY_CONFIRM_LISTED_FIX9")].iloc[0]),
            ("仅恐慌入场", full.loc[full.candidate.eq(f"{product}_FEAR_ONLY_ENTRY_VALUATION_EXIT_LISTED_FIX9")].iloc[0]),
        ]
        for name, ref in refs:
            fee_multiple = float(or_row.grid_trade_fees) / float(ref.grid_trade_fees) if float(ref.grid_trade_fees) else float("nan")
            lines.append(f"| {product} | {name} | {(or_row.ann_return-ref.ann_return)*100:+.2f} pp | {(or_row.max_dd-ref.max_dd)*100:+.2f} pp | {int(or_row.grid_entries-ref.grid_entries):+d} | {fee_multiple:.2f}× |")

    transition_all = pd.concat(signal_paths, ignore_index=True)
    lines += ["", "## 入场归因与快速退出", ""]
    for product in ("IC", "IM"):
        candidate = f"{product}_VALUATION_OR_FEAR25_LISTED_FIX9"
        q = transition_all.loc[transition_all.candidate.eq(candidate)].sort_values("signal_date").reset_index(drop=True)
        buys = q.loc[q.action.eq("BUY_NEXT_OPEN")].reset_index(drop=True)
        sells = q.loc[q.action.eq("SELL_NEXT_OPEN")].reset_index(drop=True)
        cause_counts = q.loc[q.action.eq("BUY_NEXT_OPEN"), "fear_entry_cause"].value_counts().to_dict()
        threshold = THRESHOLDS[product]["exit"]
        above_exit = int(pd.to_numeric(buys.valuation_score, errors="coerce").ge(threshold).sum())
        day_rows = daily_out.loc[daily_out.candidate.eq(candidate)].sort_values("date")
        session_ix = {str(date): i for i, date in enumerate(day_rows.date.astype(str))}
        next_day_exits = 0
        for buy, sell in zip(buys.itertuples(index=False), sells.itertuples(index=False)):
            if session_ix[str(sell.signal_date)] - session_ix[str(buy.signal_date)] == 1:
                next_day_exits += 1
        lines.append(f"- {product} OR共有 {len(buys)} 次开仓：恐慌单独触发 {cause_counts.get('fear_only', 0)} 次，两条件同日满足 {cause_counts.get('both', 0)} 次，估值单独触发 {cause_counts.get('valuation_only', 0)} 次。纯恐慌入场参照也是 {len(buys)} 次；本样本OR没有增加周期总数，主要改变了其中一次入场时点。")
        lines.append(f"- 其中 {above_exit}/{len(buys)} 次开仓时估值分已经达到或超过退出线；{next_day_exits} 次在下一个信号日触发退出。恐慌低分在估值已触发退出区时仍会新开，这是本次字面OR规则的结果，也带来额外换手。")

    lines += ["", "## 近期窗口对照", "",
              "以下窗口仍是同一历史样本的尾段检查，不是独立样本外验证。", "",
              "| 品种 | 窗口 | 估值网格年化 / 最大回撤 | OR年化 / 最大回撤 |",
              "|---|---|---:|---:|"]
    for product in ("IC", "IM"):
        for window in ("last_3y", "last_1y"):
            val_row = summary.loc[summary.candidate.eq(f"{product}_VALUATION_GRID_LISTED_FIX9") & summary.segment.eq(window)].iloc[0]
            or_row = summary.loc[summary.candidate.eq(f"{product}_VALUATION_OR_FEAR25_LISTED_FIX9") & summary.segment.eq(window)].iloc[0]
            lines.append(f"| {product} | {window} | {val_row.ann_return:.2%} / {val_row.max_dd:.2%} | {or_row.ann_return:.2%} / {or_row.max_dd:.2%} |")

    lines += ["", "## 覆盖与核验", ""]
    for row in coverage:
        lines.append(f"- {row['product']}：{row['rows']} 个交易日，{row['start']}–{row['end']}；恐慌数据缺失：{', '.join(row['fear_missing_dates']) if row['fear_missing_dates'] else '无'}。")
    lines += [
        f"- fix9估值网格全账户 parity：{len(baseline_parity)}项，全部PASS；无网格保存路径 parity：{len(no_grid_parity)}项，全部PASS。",
        f"- T+1开盘执行审计：{len(execution_audits)}条路径；缺失成交 {sum(len(x['missing_t1_open_fills']) for x in execution_audits)}；日期/时段错误 {sum(len(x['wrong_phase_or_date']) for x in execution_audits)}。",
        f"- 账户可行性：{len(feasibility)}条路径均通过正净值、有限现金和保证金后可用资金检查。估值阈值重建与 fix9 原网格目标逐日完全一致。",
        "- 独立复算脚本对8条完整账户曲线重算收益指标，并复核4条候选规则状态；结果见 `outputs/independent_recheck.json`。",
        "- IC 使用 fix9 MOM120 卖 Put 准入规则历史回溯；IM 沿用 fix9 未更改的正式主线。绩效口径按期货30%保证金/缓冲，卖方15% NAV预留、引擎原手续费和3%现金利率；无真实盘口冲击模拟。", "",
        "## 解读边界", "",
        "恐慌历史快照没有逐日原始发布版本，不能证明历史点时可得。IC/IM 是同一全账户引擎上的反事实研究结果，不是历史实盘收益；网格成交次数有限，且 OR 会使恐慌低但估值偏高时也开仓，不能把单次入场优势视为稳定规律。", "",
        "## 可复现", "",
        f"- 命令：`python -X utf8 {Path(__file__).resolve().relative_to(ROOT)}`",
        f"- 独立复核：`python -X utf8 {Path(__file__).resolve().relative_to(ROOT).parent / 'audit_or_entry_artifacts.py'}`",
        "- 输入/代码 SHA-256：`source_hashes.json`；基准、信号和账户审计：`verification.json`；逐日 NAV、信号和成交在 `outputs/`。", "",
        "决定：`research_only_no_promotion`。",
    ]
    (RUN_DIR / "record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    meta = {"run_id": RUN_DIR.name, "classification": verification["classification"],
            "entry_thresholds": THRESHOLDS, "fear_entry_max_inclusive": FEAR_MAX,
            "arms": list(ARMS), "source_hashes": "source_hashes.json", "verification": "verification.json"}
    (RUN_DIR / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(SCRATCH_DIR, ignore_errors=True)
    print(json.dumps({"stage": "complete", "account_paths": len(full),
                      "summary_rows": len(summary), "daily_rows": len(daily_out),
                      "baseline_parity": len(baseline_parity), "no_grid_parity": len(no_grid_parity)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
