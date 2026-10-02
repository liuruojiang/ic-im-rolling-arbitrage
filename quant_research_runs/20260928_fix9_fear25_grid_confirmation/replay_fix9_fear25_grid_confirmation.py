"""Re-run the valuation-grid + Fear<=25 entry-confirm overlay on the fix9 listed account."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

RUN_DIR = Path(__file__).resolve().parent
ROOT = RUN_DIR.parents[1]
FIX9_LISTED_DIR = ROOT / "quant_param_scan_runs/20260928_fix9_fear_grid_listed"
FIX9_PRELISTING_DIR = ROOT / "quant_param_scan_runs/20260928_fix9_fear_grid_rebased"
FIX9_FULLACCOUNT_DIR = ROOT / "quant_param_scan_runs/20260928_momentum_future_breakeven_fix9_asifcurrent_full_v2"
FEAR_FILE = ROOT / "quant_research_runs/20260928_csi1000_fear_greed_reproduction/inputs/fear_greed_full.csv"
OUTPUT_DIR = RUN_DIR / "outputs"
SCRATCH_DIR = RUN_DIR / "scratch"
SCOPE = "listed_quotes_fix9_asif_current"
GRID_UNITS = 0.5
FEAR_MAX = 25.0

sys.path.insert(0, str(FIX9_LISTED_DIR))
import run_fix9_fear_grid_listed_scan as listed  # noqa: E402

sys.path.insert(0, str(FIX9_FULLACCOUNT_DIR))
import run_candidate as fix9  # noqa: E402

grid = listed.grid_scan


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def build_fear_confirm_targets(risk: pd.DataFrame, fear: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    z = risk.copy()
    dates = z.signal_date.astype(str)
    base = pd.to_numeric(z.grid_target_units, errors="raise").to_numpy(float)
    if not np.isin(base, [0.0, GRID_UNITS]).all():
        raise RuntimeError(f"unexpected fix9 valuation-grid targets: {sorted(set(base))}")

    targets = np.zeros(len(z), dtype=float)
    state = 0.0
    for i, day in enumerate(dates):
        if base[i] == 0.0:
            # Exits remain controlled by the original valuation grid.
            state = 0.0
        elif state == 0.0:
            score = fear.get(day, np.nan)
            if pd.notna(score) and math.isfinite(float(score)) and float(score) <= FEAR_MAX:
                state = GRID_UNITS
        # A held grid remains held until the underlying valuation target exits.
        targets[i] = state

    z["grid_target_units"] = targets
    transitions: list[dict] = []
    state_before = 0.0
    for i, day in enumerate(dates):
        target = float(targets[i])
        if target != state_before:
            score = fear.get(day, np.nan)
            transitions.append({
                "signal_date": day,
                "expected_execution_date": str(z.execution_date.iloc[i]),
                "action": "BUY_NEXT_OPEN" if target > state_before else "SELL_NEXT_OPEN",
                "fear_greed_index": float(score) if pd.notna(score) else np.nan,
                "grid_target_before": state_before,
                "grid_target_after": target,
                "terminal_unexecuted": i == len(z) - 1,
            })
        state_before = target
    return z, pd.DataFrame(transitions, columns=[
        "signal_date", "expected_execution_date", "action", "fear_greed_index",
        "grid_target_before", "grid_target_after", "terminal_unexecuted",
    ])


def compare_no_grid_to_saved(daily: pd.DataFrame, reference_all: pd.DataFrame,
                             product: str) -> dict:
    label = f"{product}_NO_GRID_LISTED_FIX9"
    reference = reference_all.loc[reference_all.candidate.eq(label)].copy()
    if reference.empty:
        raise RuntimeError(f"saved fix9 no-grid reference missing: {label}")
    reference["date"] = reference.date.astype(str)
    if daily.date.astype(str).tolist() != reference.date.astype(str).tolist():
        raise RuntimeError(f"no-grid calendar differs from saved fix9 replay: {product}")
    cols = ("nav", "return_net", "equity_close", "cash_close", "margin_reserved",
            "option_value", "seller_buffer", "fees_cumulative")
    errors: dict[str, float] = {}
    for col in cols:
        if col not in daily or col not in reference:
            continue
        left = pd.to_numeric(daily[col], errors="coerce").to_numpy(float)
        right = pd.to_numeric(reference[col], errors="coerce").to_numpy(float)
        if not np.isfinite(left).all() or not np.isfinite(right).all():
            raise RuntimeError(f"non-finite no-grid parity values: {product}/{col}")
        errors[col] = float(np.max(np.abs(left - right)))
    strict = max((v for k, v in errors.items() if k in {"nav", "return_net"}), default=0.0)
    money = max((v for k, v in errors.items() if k not in {"nav", "return_net"}), default=0.0)
    if strict > 1e-10 or money > 1e-6:
        raise RuntimeError(f"no-grid parity failed: {product}: {errors}")
    return {"status": "PASS", "reference": label, "daily_rows": len(daily),
            "max_abs_errors": errors, "nav_return_tolerance": 1e-10,
            "cash_equity_tolerance": 1e-6}


def enrich_arm(product: str, label: str, daily: pd.DataFrame, signals: pd.DataFrame,
               events: pd.DataFrame, risk: pd.DataFrame, fear: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    fills = events.loc[
        events.event.eq("trade")
        & events.symbol.fillna("").astype(str).str.startswith("future|grid|")
    ].copy()
    fills["candidate"] = label
    fills["instrument"] = product
    fills["dataset"] = SCOPE
    if len(signals):
        signals = signals.copy()
        signals["candidate"] = label
        signals["instrument"] = product
        signals["dataset"] = SCOPE

    daily = daily.copy()
    daily["date"] = daily.date.astype(str)
    target_by_date = risk.set_index("signal_date").grid_target_units.astype(float)
    if target_by_date.index.duplicated().any():
        raise RuntimeError(f"duplicate target dates: {product}/{label}")
    daily["instrument"] = product
    daily["dataset"] = SCOPE
    daily["candidate"] = label
    daily["fear_greed_index"] = daily.date.map(fear)
    daily["grid_target_signal_units"] = target_by_date.reindex(daily.date).to_numpy()
    targets = target_by_date.reindex(daily.date).to_numpy(float)
    daily["grid_held_units"] = np.r_[0.0, targets[:-1]]
    buy_dates = set(signals.loc[signals.action.eq("BUY_NEXT_OPEN"), "signal_date"].astype(str)) if len(signals) else set()
    sell_dates = set(signals.loc[signals.action.eq("SELL_NEXT_OPEN"), "signal_date"].astype(str)) if len(signals) else set()
    daily["grid_buy_signal"] = daily.date.isin(buy_dates).astype(int)
    daily["grid_sell_signal"] = daily.date.isin(sell_dates).astype(int)
    counts = signals.action.value_counts().to_dict() if len(signals) else {}
    daily["grid_entries_total"] = int(counts.get("BUY_NEXT_OPEN", 0))
    daily["grid_exits_total"] = int(counts.get("SELL_NEXT_OPEN", 0))
    daily["grid_fill_count"] = len(fills)
    daily["grid_trade_fees_total"] = float(pd.to_numeric(fills.fee, errors="coerce").fillna(0).sum()) if len(fills) else 0.0

    execution = listed.audit_grid_execution(signals, fills, label)
    free_cash = pd.to_numeric(daily.cash_close, errors="raise") - pd.to_numeric(daily.margin_reserved, errors="raise")
    feasibility = {
        "product": product, "candidate": label,
        "minimum_nav": float(pd.to_numeric(daily.nav, errors="raise").min()),
        "minimum_close_cash": float(pd.to_numeric(daily.cash_close, errors="raise").min()),
        "minimum_free_cash_after_margin": float(free_cash.min()),
        "all_nav_cash_finite": bool(np.isfinite(daily.nav.to_numpy(float)).all()
                                     and np.isfinite(daily.cash_close.to_numpy(float)).all()),
    }
    if feasibility["minimum_nav"] <= 0 or feasibility["minimum_free_cash_after_margin"] < -1e-7:
        raise RuntimeError(f"fix9 account feasibility failed: {feasibility}")
    if events.loc[events.event.eq("trade"), "symbol"].fillna("").astype(str).str.startswith("option|call|").any():
        raise RuntimeError(f"unexpected Call trade in fix9 mainline: {product}/{label}")
    return daily, fills, {"execution": execution, "feasibility": feasibility}


def main() -> None:
    if (RUN_DIR / "verification.json").exists():
        raise FileExistsError("registered fix9 replay is already finalized; preserve and audit it")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    # Keep all temporary risk-file overrides inside this new isolated run.
    listed.SCRATCH_DIR = SCRATCH_DIR

    source, candidates, gov, spot, lifecycles, formal_folders, candidates_path, im_lifecycle_path, ic_lifecycle_audit = listed.load_account_inputs()
    fear_df = pd.read_csv(FEAR_FILE, dtype={"date": str})
    if fear_df.date.duplicated().any():
        raise RuntimeError("Fear snapshot has duplicate dates")
    fear_df["fear_greed_index"] = pd.to_numeric(fear_df.fear_greed_index, errors="coerce")
    fear = fear_df.set_index("date").fear_greed_index
    saved_daily = pd.read_csv(FIX9_LISTED_DIR / "outputs/listed_fix9_daily_nav.csv.gz", dtype={"date": str})

    all_daily: list[pd.DataFrame] = []
    all_signals: list[pd.DataFrame] = []
    all_fills: list[pd.DataFrame] = []
    all_metrics: list[dict] = []
    coverage: list[dict] = []
    baseline_parity: list[dict] = []
    no_grid_parity: list[dict] = []
    execution_audits: list[dict] = []
    feasibility: list[dict] = []
    missing_fear: dict[str, list[str]] = {}
    signal_rules: list[dict] = []

    for product in ("IC", "IM"):
        folder = formal_folders[product]
        risk_path = folder / f"native_fix4_{product.lower()}_risk_signals_v1.csv.gz"
        targets_path = folder / f"native_fix4_{product.lower()}_noseller_targets_{'v2' if product == 'IC' else 'v3'}.csv.gz"
        risk = pd.read_csv(risk_path, dtype={"signal_date": str, "execution_date": str})
        missing_exec = risk.execution_date.isna()
        if risk.signal_date.duplicated().any() or (missing_exec.any() and not missing_exec.iloc[-1]):
            raise RuntimeError(f"duplicate signal dates or nonterminal missing T+1 execution dates: {product}")
        missing_fear[product] = [d for d in risk.signal_date.astype(str) if pd.isna(fear.get(d, np.nan))]
        coverage.append({"product": product, "rows": int(len(risk)), "start": str(risk.signal_date.iloc[0]),
                         "end": str(risk.signal_date.iloc[-1]), "fear_missing_dates": missing_fear[product],
                         "risk_signals_sha256": sha256(risk_path), "no_seller_targets_sha256": sha256(targets_path)})

        variants: list[tuple[str, pd.DataFrame, pd.DataFrame]] = []
        valuation_risk, valuation_signals = grid.build_grid_targets(
            risk, fear, {"kind": "valuation", "label": "VALUATION_GRID"}, GRID_UNITS
        )
        no_grid_risk, no_grid_signals = grid.build_grid_targets(
            risk, fear, {"kind": "no_grid", "label": "NO_GRID"}, GRID_UNITS
        )
        confirm_risk, confirm_signals = build_fear_confirm_targets(risk, fear)
        variants.extend([
            ("NO_GRID", no_grid_risk, no_grid_signals),
            ("VALUATION_GRID", valuation_risk, valuation_signals),
            ("FEAR25_ENTRY_CONFIRM", confirm_risk, confirm_signals),
        ])

        for arm, variant_risk, signals in variants:
            label = f"{product}_{arm}_LISTED_FIX9"
            daily, events, replay_audit = listed.replay(
                product, variant_risk, targets_path, lifecycles[product],
                source, candidates, gov, spot,
            )
            daily["date"] = daily.date.astype(str)
            events["date"] = events.date.astype(str)
            if len(daily) != len(variant_risk) or daily.date.tolist() != variant_risk.signal_date.astype(str).tolist():
                raise RuntimeError(f"fix9 listed replay calendar mismatch: {label}")

            if arm == "VALUATION_GRID":
                baseline_parity.append({"product": product, "candidate": label,
                                        **fix9.assert_baseline_parity(product, daily, events)})
            elif arm == "NO_GRID":
                no_grid_parity.append({"product": product,
                                       **compare_no_grid_to_saved(daily, saved_daily, product)})

            daily, fills, checks = enrich_arm(product, label, daily, signals, events, variant_risk, fear)
            execution_audits.append({"product": product, "candidate": label, **checks["execution"]})
            feasibility.append(checks["feasibility"])
            for row in grid.compute_windows(daily, label, SCOPE, product, signals, fills):
                row.update({"fear_entry_max": FEAR_MAX if arm == "FEAR25_ENTRY_CONFIRM" else "",
                            "grid_units": GRID_UNITS,
                            "account_grid_trade_fills": int(len(fills)),
                            "account_grid_trade_fees_total": float(daily.grid_trade_fees_total.iloc[0])})
                all_metrics.append(row)
            signals = signals.copy()
            if len(signals):
                signals["candidate"] = label
                signals["instrument"] = product
                signals["dataset"] = SCOPE
            else:
                signals = signals.copy()
                signals["candidate"] = pd.Series(dtype=str)
                signals["instrument"] = pd.Series(dtype=str)
                signals["dataset"] = pd.Series(dtype=str)
            all_signals.append(signals)
            if arm == "FEAR25_ENTRY_CONFIRM":
                signals.to_csv(OUTPUT_DIR / f"{product.lower()}_fear25_grid_signals.csv", index=False)
            if len(fills):
                all_fills.append(fills)
            all_daily.append(daily)
            signal_rules.append({"product": product, "candidate": label,
                                 "risk_target_sha256": sha256(risk_path),
                                 "signal_count": int(len(signals)),
                                 "buy_signals": int((signals.action == "BUY_NEXT_OPEN").sum()) if len(signals) else 0,
                                 "sell_signals": int((signals.action == "SELL_NEXT_OPEN").sum()) if len(signals) else 0,
                                 "underlying_valuation_target_positive_days": int(
                                     pd.to_numeric(risk.grid_target_units, errors="raise").gt(0).sum()),
                                 "candidate_grid_target_positive_days": int(
                                     pd.to_numeric(variant_risk.grid_target_units, errors="raise").gt(0).sum())})
            print(json.dumps({"stage": "arm_done", "candidate": label,
                              "rows": len(daily), "signals": len(signals),
                              "fills": len(fills)}, ensure_ascii=False), flush=True)

    daily_out = pd.concat(all_daily, ignore_index=True)
    summary = pd.DataFrame(all_metrics)
    daily_out.to_csv(OUTPUT_DIR / "fix9_full_account_daily_nav.csv.gz", index=False, compression="gzip")
    if all_signals:
        pd.concat(all_signals, ignore_index=True).to_csv(OUTPUT_DIR / "grid_transition_signals.csv", index=False)
    if all_fills:
        pd.concat(all_fills, ignore_index=True).to_csv(OUTPUT_DIR / "grid_account_fills.csv", index=False)
    summary.to_csv(RUN_DIR / "portfolio_summary.csv", index=False)

    study = fix9.study
    source_paths = [
        Path(__file__).resolve(), RUN_DIR / "preregistered_spec.md",
        Path(listed.__file__), Path(grid.__file__), Path(fix9.__file__),
        FIX9_LISTED_DIR / "outputs/listed_fix9_daily_nav.csv.gz",
        FIX9_LISTED_DIR / "verification.json", FIX9_LISTED_DIR / "source_hashes.json",
        FIX9_FULLACCOUNT_DIR / "native_account_breakeven_candidate.py",
        FIX9_FULLACCOUNT_DIR / "baseline_daily.csv.gz", FIX9_FULLACCOUNT_DIR / "baseline_events.csv.gz",
        FIX9_FULLACCOUNT_DIR / "audit.json", fix9.IC_FIX9_RUN / "daily_outputs.csv.gz",
        fix9.IC_FIX9_RUN / "events.csv.gz", fix9.STUDY / "native_account.py",
        Path(study.base.__file__), Path(study.seller_lifecycle.__file__),
        Path(study.FORMAL.v14_policy.__file__),
        study.NATIVE / "native_fix4_seller_candidates_v1.csv.gz",
        FEAR_FILE, listed.GOV_FILE, candidates_path, im_lifecycle_path,
        study.base.OHLCV["IC"], study.base.OHLCV["IM"],
    ]
    for product in ("IC", "IM"):
        folder = formal_folders[product]
        source_paths += [folder / f"native_fix4_{product.lower()}_risk_signals_v1.csv.gz",
                         folder / f"native_fix4_{product.lower()}_noseller_targets_{'v2' if product == 'IC' else 'v3'}.csv.gz",
                         folder / "native_fix4_seller_lifecycle_v4.csv.gz"]
    source_hashes = {str(p.relative_to(ROOT)): sha256(p) for p in source_paths if p.is_file()}
    (RUN_DIR / "source_hashes.json").write_text(json.dumps(source_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    verification = {
        "classification": "fix9_as_if_current_listed_full_account_overlay_research_only",
        "run_id": RUN_DIR.name,
        "strategy_identity": "v1.4-r1 fix9 as-if-current full-account replay",
        "ic_rule": "fix9 MOM120 seller-admission rule applied retrospectively",
        "im_rule": "unchanged formal IM path under the fix9 full-account engine",
        "fear_rule": "new entry only when original valuation target is 0.5 and close Fear<=25; held position exits only when original valuation target becomes zero",
        "missing_fear_rule": "fail closed for new entry; existing position and valuation exit unchanged",
        "execution": "T close signal, T+1 listed futures open fill",
        "scope": SCOPE,
        "coverage": coverage,
        "missing_fear_dates": missing_fear,
        "fix9_ic_seller_lifecycle_audit": ic_lifecycle_audit,
        "valuation_grid_fix9_baseline_parity": baseline_parity,
        "no_grid_saved_fix9_replay_parity": no_grid_parity,
        "grid_execution_audits": execution_audits,
        "account_feasibility": feasibility,
        "signal_counts": signal_rules,
        "fees_financing": {"source": "fix9 native account engine", "grid_units": GRID_UNITS,
                           "futures_margin_buffer": "30% performance basis",
                           "seller_reserve": "15% NAV feasibility ceiling",
                           "cash_rate": "3% annual in native engine",
                           "separate_grid_carry": "none; grid shares account futures quote stream and ledger"},
        "fear_snapshot_sha256": sha256(FEAR_FILE),
        "point_in_time_fear_history_proven": False,
        "source_hashes": "source_hashes.json",
        "outputs": ["portfolio_summary.csv", "outputs/fix9_full_account_daily_nav.csv.gz",
                    "outputs/grid_transition_signals.csv", "outputs/grid_account_fills.csv"],
        "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }
    (RUN_DIR / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    full = summary.loc[summary.segment.eq("full")]
    table = ["| 品种 | 全账户路径 | 年化收益 | 累计收益 | 最大回撤 | 网格开/平 | 持仓日 | 网格手续费 |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for product in ("IC", "IM"):
        for arm in ("NO_GRID", "VALUATION_GRID", "FEAR25_ENTRY_CONFIRM"):
            label = f"{product}_{arm}_LISTED_FIX9"
            row = full.loc[full.candidate.eq(label)].iloc[0]
            table.append(f"| {product} | {arm} | {row.ann_return:.2%} | {row.total_return:.2%} | {row.max_dd:.2%} | {int(row.grid_entries)}/{int(row.grid_exits)} | {int(row.grid_held_days)} | {row.grid_trade_fees:,.0f} |")
    lines = [
        "# Fix9：估值网格 + 恐慌≤25入场确认复跑", "",
        "状态：研究候选；仅历史反事实全账户回放，不代表真实账户成交或生产授权。", "",
        "## 结论", "",
        "此前叠加回测的 IC/IM 基准不是 fix9：IC 引用 v1.3/R7，IM 的其他组合部件冻结于 2026-09-07。以下结果是改用 fix9 账户引擎、当前可核验挂牌期输入后重新计算的结果；旧数值不应当作 fix9 结论。", "",
        "本候选只在估值网格仍允许开仓且空仓时增加恐慌分≤25确认；恐慌不决定退出，持仓退出继续按原估值网格。", "",
        "## 同期全账户对照", "", "年化以同次运行全账户 `return_net` 的全部 N 个日收益计算；最大回撤由相同全账户净值计算。", "",
        *table, "",
        "## 数据与基准", "",
    ]
    for c in coverage:
        lines.append(f"- {c['product']}：{c['rows']} 个交易日，{c['start']}–{c['end']}；恐慌值缺失日：{', '.join(c['fear_missing_dates']) if c['fear_missing_dates'] else '无'}。")
    lines += [
        "- IC 使用 fix9 MOM120 卖 Put 准入规则回溯；IM 使用 fix9 全账户引擎下未变更的正式 IM 路径。两个产品都重放核心期货、动量期货、Put、卖 Put、现金、保证金、费用和其他账户状态。",
        "- 无网格结果与先前 fix9 挂牌扫描的保存日线逐日对账；估值网格结果与 fix9 已保存基准逐日及逐事件对账。", "",
        "## 审计", "",
        f"- 估值网格 fix9 基准 parity：{len(baseline_parity)} 项，全部 PASS。",
        f"- 无网格保存路径 parity：{len(no_grid_parity)} 项，全部 PASS。",
        f"- T+1 开盘成交审计：{len(execution_audits)} 条路径；缺失成交 {sum(len(x['missing_t1_open_fills']) for x in execution_audits)}；日期/时段错误 {sum(len(x['wrong_phase_or_date']) for x in execution_audits)}。",
        f"- 账户可行性：{len(feasibility)} 条路径 NAV、现金和保证金后可用资金均通过。",
        "- 使用期货 30%保证金/缓冲作为绩效口径、卖方15% NAV预留、引擎原手续费和3%现金利率；未另加网格 carry，也没有真实盘口冲击建模。", "",
        "## 限制", "",
        "恐慌序列是当前下载的历史快照，没有逐日原始发布版本，故不能证明历史时点可用性。IC 的 fix9 规则在历史期间按当前规则反事实回放；所有结果都是模型账户研究数据，不是历史实盘收益。可核验的挂牌期截至 IC 2026-08-13、IM 2026-08-14；不把旧 IM 至 2026-09-07 或旧 IC 至 2026-08-14 的尾部混入。", "",
        "## 可复现", "",
        f"- 命令：`python -X utf8 {Path(__file__).resolve().relative_to(ROOT)}`",
        "- 输入、策略实现和运行脚本 SHA-256：`source_hashes.json`。完整验证：`verification.json`。逐日账户曲线、网格信号与成交分别见 `outputs/`。", "",
        "决定：`research_only_no_promotion`。",
    ]
    (RUN_DIR / "record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_meta = {"run_id": RUN_DIR.name, "classification": verification["classification"],
                "threshold": {"entry_fear_max_inclusive": FEAR_MAX}, "scope": SCOPE,
                "source_hashes": "source_hashes.json", "verification": "verification.json"}
    (RUN_DIR / "run_meta.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    import shutil
    shutil.rmtree(SCRATCH_DIR, ignore_errors=True)
    print(json.dumps({"stage": "complete", "summary_rows": len(summary),
                      "daily_rows": len(daily_out), "baseline_parity": len(baseline_parity),
                      "no_grid_parity": len(no_grid_parity),
                      "missing_fear_dates": missing_fear}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
