"""Build one IC/IM v1.4 signal artifact for GitHub Actions.

The runner owns no scheduler and sends no email.  It restores/updates the
hash-chained ledger through :class:`StateStore`.  Close-confirmed reports must
match the just-committed ledger record.  Realtime reports must be generated
during the continuous session from the latest completed ledger and may never
mutate that ledger.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import poe_ic_im_mainline_v1_4_bot as strategy
import poe_ic_im_v1_4_state as state_module
from poe_ic_im_v1_4_server import LedgerCoordinator
from poe_ic_im_v1_4_state import StateStore, _jsonable


PRODUCTS = ("IC", "IM")
FIX4_DELIVERY_REVISION = "20260924-v14-coreput3x-fixedshort95-fix4-integrated-iciv30-qdelta05"
FIX6_DELIVERY_REVISION = "20260926-v14-coreput3x-fixedshort95-fix6-nocall-repeatroll-iciv30-qdelta05"
FIX7_DELIVERY_REVISION = "20260928-v14-coreput3x-open-fix7-nocall-repeatroll-iciv30-qdelta05"
DELIVERY_REVISION = "20260929-v14-ordinary-put-open-ic-seller-mom120-fix9"
MODES = ("close", "realtime")


def delivery_revision_for_signal_day(signal_day: date) -> str:
    if signal_day >= strategy.v14_policy.ORDINARY_PUT_OPEN_EFFECTIVE_SIGNAL_DATE:
        return DELIVERY_REVISION
    if signal_day >= strategy.v14_policy.PROFIT_OPEN_EFFECTIVE_SIGNAL_DATE:
        return FIX7_DELIVERY_REVISION
    if signal_day >= strategy.v14_policy.FIX6_BUILD_EFFECTIVE_SIGNAL_DATE:
        return FIX6_DELIVERY_REVISION
    return FIX4_DELIVERY_REVISION


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_clock(value: str) -> datetime:
    if not value.strip():
        return strategy._now_beijing()
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=strategy.BEIJING)
    return parsed.astimezone(strategy.BEIJING)


def _signal_day(signal: dict[str, Any]) -> date:
    value = signal.get("market_date")
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def validate_close_artifact(
    *,
    completed_day: date,
    latest: dict[str, Any],
    observed: dict[str, dict[str, Any]],
) -> None:
    if set(observed) != set(PRODUCTS):
        raise RuntimeError("收盘产物必须同时包含IC和IM完整信号")
    signal_days = {_signal_day(observed[product]) for product in PRODUCTS}
    if signal_days != {completed_day}:
        raise RuntimeError(
            f"收盘信号日期不一致：期望 {completed_day}，实际 {sorted(signal_days)}"
        )
    for product in PRODUCTS:
        signal = observed[product]
        state_module.validate_delivery_values(signal, product)
        if not bool(signal.get("close_confirmed")):
            raise RuntimeError(f"{product}不是收盘确认信号")
        if str(signal.get("product")) != product:
            raise RuntimeError(f"{product}信号产品标签不一致")
    if str(latest.get("verified_day"))[:10] != completed_day.isoformat():
        raise RuntimeError("持久账本尚未推进到最近完成交易日")
    if _jsonable(observed) != latest.get("signals"):
        raise RuntimeError("邮件信号与持久账本最新逐腿记录不一致")
    if completed_day >= date(2026, 9, 7):
        state_module.validate_im_put_execution_evidence(observed["IM"])


def validate_realtime_artifact(
    *,
    clock: datetime,
    completed_day: date,
    before: dict[str, Any],
    after: dict[str, Any],
    observed: dict[str, dict[str, Any]],
) -> None:
    if strategy._market_phase(clock) != "盘中":
        raise RuntimeError("盘中实时邮件只能在连续交易时段生成")
    if set(observed) != set(PRODUCTS):
        raise RuntimeError("盘中产物必须同时包含IC和IM完整信号")
    signal_days = {_signal_day(observed[product]) for product in PRODUCTS}
    if signal_days != {clock.date()}:
        raise RuntimeError(
            f"盘中信号日期不一致：期望 {clock.date()}，实际 {sorted(signal_days)}"
        )
    for product in PRODUCTS:
        signal = observed[product]
        state_module.validate_delivery_values(signal, product)
        if bool(signal.get("close_confirmed")):
            raise RuntimeError(f"{product}盘中信号被错误标记为收盘确认")
        if str(signal.get("product")) != product:
            raise RuntimeError(f"{product}信号产品标签不一致")
        if str(signal.get("market_phase")) != "盘中":
            raise RuntimeError(f"{product}行情不处于连续交易时段")
        if str(signal.get("state_anchor_day"))[:10] != completed_day.isoformat():
            raise RuntimeError(f"{product}没有从最近完成交易日账本续接")
    if str(before.get("verified_day"))[:10] != completed_day.isoformat():
        raise RuntimeError("持久账本尚未推进到最近完成交易日")
    if (
        before.get("digest") != after.get("digest")
        or before.get("sequence") != after.get("sequence")
        or before.get("verified_day") != after.get("verified_day")
    ):
        raise RuntimeError("盘中信号不得改写持久收盘账本")
    if clock.date() >= date(2026, 9, 7):
        state_module.validate_im_put_execution_evidence(observed["IM"])


def _execution_timing_lines(product: str, signal: dict[str, Any]) -> list[str]:
    """Render execution timing separately from signal targets.

    A close report contains both next-open sleeve targets and quarterly futures
    roll state.  They must not be presented as one undifferentiated action.
    """
    next_trade_day = signal.get("next_trade_date", "N/A")
    lines = [
        "- 常规仓位（动量／网格）：今日收盘形成目标；仅在目标变化时，于 "
        f"{next_trade_day} 开盘执行。",
    ]
    if signal.get("roll_policy", {}).get("tenor") != "strict_quarter":
        lines.append(
            "- 季度期货展期：本记录早于 r7 生效日；2026-09-04 起才采用 "
            "IM T-1／IC T-3 收盘展期。"
        )
        return lines

    execution_day = signal.get("roll_execution_date", "N/A")
    current = signal.get("core_current", "N/A")
    destination = signal.get("core_eod_contract", "N/A")
    if signal.get("roll_confirmed"):
        lines.append(
            f"- 季度期货展期：**已于 {execution_day} 收盘完成**，"
            f"{current} → {destination}；不是下一交易日开盘动作。"
        )
    elif signal.get("core_action") == "ROLL":
        lines.append(
            f"- 季度期货展期：**仅预告**，计划于 {execution_day} 收盘执行，"
            f"{current} → {signal.get('core_target', 'N/A')}；尚未写入账本。"
        )
    else:
        lines.append(
            f"- 季度期货展期：当前无执行；下一计划日为 {execution_day} 收盘，"
            f"当前仍为 {current}。"
        )
    lines.append("- Put／Call：独立于季度期货展期，按各自维护日历处理。")
    return lines


def render_stored_close_report(latest: dict[str, Any]) -> str:
    signals = latest.get("signals", {})
    lines = [
        "# IC / IM 1.4 收盘确认账本",
        "",
        f"本记录信号构建：`{signals.get('IC', {}).get('v14_build_id', 'N/A')}`；当前程序构建：`{strategy.BUILD_ID}`；网格参数版本：`{strategy.GRID_POLICY_REVISION}`；动量防抖版本：`{strategy.MOMENTUM_DEBOUNCE_POLICY_REVISION}`。",
        "卖Put展期规则：2026-09-25及以前信号最多成功展期一次；2026-09-26及以后信号每次满足条件可继续展期。本记录以逐腿信号版本为准。",
        "IM卖Call规则：2026-09-25及以前信号保留旧版；2026-09-26及以后信号目标为空仓，若模型账本有旧仓只输出平仓目标。本记录以逐腿信号版本为准。",
        "自2026-09-16信号日起，IC与IM的核心Put在MOM120<0时立即保护，连续两日均>+1%才解除；Abs20≤0立即降至半仓，连续两日均>+1%才恢复满仓；Score不变。",
        "网格新版本：2026-09-15信号日起，IM恢复1.6进入/2.0退出、0.5倍，仅估值；IC保持0.5进入/1.0退出、0.5倍。此前信号保留当日规则。",
        "本附件直接来自已通过SHA-256日志链校验的持久账本，不进行第二次联网重算。",
        "它是研究审计记录，不是账户持仓，也不会自动下单。",
        "",
        f"- 已核验日期：`{latest.get('verified_day', 'N/A')}`",
        f"- 账本序号：`{latest.get('sequence', 'N/A')}`",
        f"- 账本摘要：`{latest.get('digest', 'N/A')}`",
    ]
    for product in PRODUCTS:
        signal = signals[product]
        profit_detail = []
        if signal.get("v14_profit_reentry_status") == "scheduled_t_plus_1_open":
            profit_detail.append(
                f"- 3倍兑现次日开盘计划：{signal.get('v14_profit_execution_day')} 卖旧 "
                f"{signal.get('v14_profit_old_contract')}、买预选 "
                f"{signal.get('v14_profit_reentry_contract')}，数量 "
                f"{signal.get('v14_profit_reentry_qty')}；尚非成交。"
            )
        elif signal.get("v14_profit_reentry_status") == "confirmed_open_research_price":
            profit_detail.append(
                f"- 3倍兑现开盘纸面确认：{signal.get('v14_profit_open_price_day')} 旧 "
                f"{signal.get('v14_profit_open_old_contract')} @ {signal.get('v14_profit_exit_open_price')}；"
                f"新 {signal.get('v14_profit_open_executed_contract')} @ "
                f"{signal.get('v14_profit_reentry_entry_premium')}，数量 "
                f"{signal.get('v14_profit_open_executed_qty')}；非账户成交。"
            )
        ordinary = signal.get("v14_ordinary_put_pending")
        if signal.get("v14_ordinary_put_plan_status") == "scheduled_t_plus_1_open" and ordinary:
            profit_detail.append(
                f"- 普通核心/动量买Put：{ordinary.get('signal_day')}收盘预选，"
                f"{ordinary.get('execution_day')}开盘待确认；尚非纸面成交。"
            )
        if signal.get("v14_ordinary_put_open_status") == "confirmed_open_research_price":
            profit_detail.append("- 普通核心/动量买Put：按预选合约及当日正开盘价完成纸面确认；非账户成交。")
        elif str(signal.get("v14_ordinary_put_open_status", "")).startswith("closed_"):
            profit_detail.append(
                f"- 普通买Put前次开盘计划关闭：{signal['v14_ordinary_put_open_status']}；"
                f"{signal.get('v14_ordinary_put_open_reason', '未取得完整开盘依据')}。未记纸面成交。"
            )
        expiry_branches = signal.get("v14_expiry_conditional_signal")
        expiry_line = (
            "- 卖Put到期条件信号：模型结算依据尚待核验；价外失效→结束卖Put周期并返回普通期货路线；"
            "价内行权/现金结算→进入模型承接与恢复路线。实际账户操作由用户自行处理。"
            if isinstance(expiry_branches, dict)
            else ""
        )
        lines.extend(
            [
                "",
                f"## {product}",
                str(signal.get("iv_warning", {}).get("text", "IV预警：N/A（历史记录未保存IV监测值）")),
                str(signal.get("im_put_policy_description", "")),
                "",
                "**" + strategy.daily_valuation.disclosure(signal.get("valuation_provenance")) + "**",
                "",
                "- " + strategy.quarter_roll.format_spread(signal.get("quarter_spread")),
                *_execution_timing_lines(product, signal),
                f"- 期货总仓：{signal.get('total_units_current', 'N/A')} → "
                f"{signal.get('total_units_target', 'N/A')}",
                f"- 核心动作：`{signal.get('core_action', 'N/A')}`；"
                f"动量动作：`{signal.get('momentum_action', 'N/A')}`；"
                f"网格动作：`{signal.get('grid_action', 'N/A')}`",
                f"- v1.4核心Put：{signal.get('v14_core_put_contract') or signal.get('core_put_target_contract') or 'N/A'}；数量：{signal.get('v14_core_put_qty', 'N/A')}；兑现状态：{signal.get('v14_profit_reentry_status', 'N/A')}；模型生命周期：{signal.get('v14_lifecycle_evidence_status', 'N/A')}",
                "- 买Put时点：3倍兑现自2026-09-28起T收盘预选、T+1开盘核价；普通核心/动量Put自2026-09-29起同样执行。月度维护按原定当日收盘，季度期货展期时钟不变。",
                *profit_detail,
                "- 信号边界：本报告只发布策略参考信号；实际成交、行权、结算、交割和账户持仓由用户自行处理。",
                expiry_line,
                f"- Put动作：`{signal.get('put_action', 'N/A')}`；"
                f"Call动作：`{signal.get('call_action', 'N/A')}`",
                "",
                "<details><summary>完整逐腿JSON</summary>",
                "",
                "```json",
                json.dumps(_jsonable(signal), ensure_ascii=False, indent=2),
                "```",
                "",
                "</details>",
            ]
        )
    return "\n".join(lines) + "\n"


def build_artifacts(
    *,
    state_dir: Path,
    out_dir: Path,
    clock: datetime,
    max_sessions: int,
    mode: str = "close",
    expected_market_date: str = "",
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"unsupported mode: {mode}")
    if max_sessions <= 0:
        raise ValueError("max_sessions必须为正")
    if mode == "realtime" and strategy._market_phase(clock) != "盘中":
        raise RuntimeError("盘中实时邮件只能在连续交易时段生成")
    completed_day = strategy._latest_completed_exchange_day(clock)
    actual_market_date = clock.date() if mode == "realtime" else completed_day
    if expected_market_date and actual_market_date.isoformat() != expected_market_date:
        raise RuntimeError(
            f"日报日期不匹配：expected={expected_market_date} actual={actual_market_date}"
        )
    store = StateStore(state_dir)
    coordinator = LedgerCoordinator(store)
    advanced = coordinator.catch_up_until_current(clock, max_sessions=max_sessions)
    health = coordinator.health(clock)
    if health.get("status") != "ok":
        raise RuntimeError(str(health.get("refresh_error") or "账本补写状态异常"))
    if str(health.get("verified_day"))[:10] != completed_day.isoformat():
        raise RuntimeError(
            "账本未追平最近完成交易日："
            f"verified={health.get('verified_day')} completed={completed_day}"
        )

    latest = store.load_latest()
    if mode == "realtime":
        if strategy._market_phase(clock) != "盘中":
            raise RuntimeError("盘中实时邮件只能在连续交易时段生成")
        report, attachments, observed = coordinator.execute_query(
            "实时信号", clock, persist_confirmed=False
        )
        _atomic_write_text(out_dir / "diagnostic_report.md", report)
        after = store.load_latest()
        if set(observed) != set(PRODUCTS):
            failures = re.findall(r"完整信号失败：([^\n]+)", report)
            raise RuntimeError(
                "盘中产物必须同时包含IC和IM完整信号；已取得="
                + ",".join(sorted(observed))
                + "；逐品种失败=" + "；".join(failures)
                + "；完整诊断见 diagnostic_report.md"
            )
        validate_realtime_artifact(
            clock=clock,
            completed_day=completed_day,
            before=latest,
            after=after,
            observed=observed,
        )
        report_name = "ic_im_v1_4_realtime_signal.md"
        publication_mode = "realtime"
        signal_day = clock.date()
    else:
        observed = latest.get("signals", {})
        validate_close_artifact(
            completed_day=completed_day,
            latest=latest,
            observed=observed,
        )
        report = render_stored_close_report(latest)
        attachments = []
        report_name = "ic_im_v1_4_close_signal.md"
        publication_mode = "close_confirmed"
        signal_day = completed_day

    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / report_name
    _atomic_write_text(report_path, report)
    result = {
        "status": "ok",
        "delivery_revision": delivery_revision_for_signal_day(signal_day),
        "strategy": "IC/IM research signal 1.4",
        "strategy_revision": state_module.STRATEGY_REVISION,
        "build": observed["IC"].get("v14_build_id"),
        "signal_build": observed["IC"].get("v14_build_id"),
        "signal_rule_revision": observed["IC"].get("v14_rule_revision"),
        "grid_policy_revision": strategy.GRID_POLICY_REVISION,
        "momentum_debounce_policy_revision": strategy.MOMENTUM_DEBOUNCE_POLICY_REVISION,
        "momentum_debounce_effective_date": strategy.MOMENTUM_DEBOUNCE_EFFECTIVE_DATE.isoformat(),
        "grid_release_note": "2026-09-15信号日起：IM恢复1.6进入/2.0退出、0.5倍，仅估值；IC保持0.5进入/1.0退出、0.5倍。此前信号保留当日规则，当前仓位以账本为准。",
        "im_put_policy_revision": strategy.IM_PUT_POLICY_REVISION,
        "im_execution_fix_revision": strategy.IM_EXECUTION_FIX_REVISION,
        "im_put_execution_revision": observed["IM"].get(
            "im_put_execution_revision", "historical_before_monthly_correction"
        ),
        "generated_at": clock.isoformat(),
        "publication_mode": publication_mode,
        "market_date": signal_day.isoformat(),
        "completed_day": completed_day.isoformat(),
        "next_trade_day": str(observed["IC"].get("next_trade_date")),
        "advanced_sessions": advanced,
        "verified_day": str(latest["verified_day"])[:10],
        "sequence": int(latest["sequence"]),
        "digest": str(latest["digest"]),
        "report_file": report_path.name,
        "attachments": [str(item.get("name", "")) for item in attachments],
        "signals": _jsonable(observed),
    }
    _atomic_write_text(
        out_dir / "result.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    (out_dir / "failure.txt").unlink(missing_ok=True)
    return result


def write_failure(
    out_dir: Path, clock: datetime, exc: Exception, *, mode: str = "close",
    expected_market_date: str = "",
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale_name in (
        "ic_im_v1_4_close_signal.md",
        "ic_im_v1_4_realtime_signal.md",
    ):
        (out_dir / stale_name).unlink(missing_ok=True)
    try:
        signal_day = (date.fromisoformat(expected_market_date) if expected_market_date else
                      clock.date() if mode == "realtime" else strategy._latest_completed_exchange_day(clock))
    except (ValueError, RuntimeError):
        signal_day = clock.date()
    payload = {
        "status": "failed",
        "delivery_revision": delivery_revision_for_signal_day(signal_day),
        "strategy": "IC/IM research signal 1.4",
        "strategy_revision": state_module.STRATEGY_REVISION,
        "build": strategy.v14_policy.identity_for_signal_day(signal_day)[0],
        "market_date": signal_day.isoformat(),
        "im_put_policy_revision": strategy.IM_PUT_POLICY_REVISION,
        "im_execution_fix_revision": strategy.IM_EXECUTION_FIX_REVISION,
        "generated_at": clock.isoformat(),
        "publication_mode": "realtime" if mode == "realtime" else "close_confirmed",
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
    _atomic_write_text(
        out_dir / "result.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    _atomic_write_text(out_dir / "failure.txt", f"{type(exc).__name__}: {exc}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--now", default="")
    parser.add_argument("--max-sessions", type=int, default=20)
    parser.add_argument("--mode", choices=MODES, default="close")
    parser.add_argument("--expected-market-date", default="")
    parser.add_argument("--network-budget-per-product", type=float, default=None)
    args = parser.parse_args()
    if args.max_sessions <= 0:
        raise SystemExit("--max-sessions must be positive")

    clock = parse_clock(args.now)
    out_dir = Path(args.out_dir)
    try:
        with strategy.collection_clock(clock), strategy.signal_product_budget_override(
            args.network_budget_per_product
        ):
            result = build_artifacts(
                state_dir=Path(args.state_dir),
                out_dir=out_dir,
                clock=clock,
                max_sessions=args.max_sessions,
                mode=args.mode,
                expected_market_date=args.expected_market_date,
            )
    except Exception as exc:
        write_failure(out_dir, clock, exc, mode=args.mode,
                      expected_market_date=args.expected_market_date)
        print(f"ic_im_digest_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        "ic_im_digest_ok: "
        f"mode={result['publication_mode']} "
        f"verified_day={result['verified_day']} sequence={result['sequence']} "
        f"build={result['build']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
