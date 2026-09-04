"""Build one IC/IM v1.3 signal artifact for GitHub Actions.

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

import poe_ic_im_mainline_v1_3_bot as strategy
import poe_ic_im_v1_3_state as state_module
from poe_ic_im_v1_3_server import LedgerCoordinator
from poe_ic_im_v1_3_state import StateStore, _jsonable


PRODUCTS = ("IC", "IM")
DELIVERY_REVISION = "20260904-crossday-transport-2"
MODES = ("close", "realtime")


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


def render_stored_close_report(latest: dict[str, Any]) -> str:
    signals = latest.get("signals", {})
    lines = [
        "# IC / IM 1.3 收盘确认账本",
        "",
        "本附件直接来自已通过SHA-256日志链校验的持久账本，不进行第二次联网重算。",
        "它是研究审计记录，不是账户持仓，也不会自动下单。",
        "",
        f"- 已核验日期：`{latest.get('verified_day', 'N/A')}`",
        f"- 账本序号：`{latest.get('sequence', 'N/A')}`",
        f"- 账本摘要：`{latest.get('digest', 'N/A')}`",
    ]
    for product in PRODUCTS:
        signal = signals[product]
        lines.extend(
            [
                "",
                f"## {product}",
                "",
                "- " + strategy.quarter_roll.format_spread(signal.get("quarter_spread")),
                (f"- 季度换仓：{product} T-{strategy.quarter_roll.ROLL_DAYS[product]}，"
                 f"计划执行日 {signal.get('roll_execution_date', 'N/A')} 收盘；期权维护独立")
                if signal.get("roll_policy", {}).get("tenor") == "strict_quarter"
                else "- 本记录行情日早于r7生效日；2026-09-04起IM季度T-1、IC季度T-3，旧记录不追改。",
                f"- 期货总仓：{signal.get('total_units_current', 'N/A')} → "
                f"{signal.get('total_units_target', 'N/A')}",
                f"- 核心动作：`{signal.get('core_action', 'N/A')}`；"
                f"动量动作：`{signal.get('momentum_action', 'N/A')}`；"
                f"网格动作：`{signal.get('grid_action', 'N/A')}`",
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
        report_name = "ic_im_v1_3_realtime_signal.md"
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
        report_name = "ic_im_v1_3_close_signal.md"
        publication_mode = "close_confirmed"
        signal_day = completed_day

    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / report_name
    _atomic_write_text(report_path, report)
    result = {
        "status": "ok",
        "delivery_revision": DELIVERY_REVISION,
        "strategy": "IC/IM research candidate 1.3",
        "strategy_revision": state_module.STRATEGY_REVISION,
        "build": strategy.BUILD_ID,
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
    out_dir: Path, clock: datetime, exc: Exception, *, mode: str = "close"
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale_name in (
        "ic_im_v1_3_close_signal.md",
        "ic_im_v1_3_realtime_signal.md",
    ):
        (out_dir / stale_name).unlink(missing_ok=True)
    payload = {
        "status": "failed",
        "delivery_revision": DELIVERY_REVISION,
        "strategy": "IC/IM research candidate 1.3",
        "strategy_revision": state_module.STRATEGY_REVISION,
        "build": strategy.BUILD_ID,
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
    args = parser.parse_args()
    if args.max_sessions <= 0:
        raise SystemExit("--max-sessions must be positive")

    clock = parse_clock(args.now)
    out_dir = Path(args.out_dir)
    try:
        result = build_artifacts(
            state_dir=Path(args.state_dir),
            out_dir=out_dir,
            clock=clock,
            max_sessions=args.max_sessions,
            mode=args.mode,
            expected_market_date=args.expected_market_date,
        )
    except Exception as exc:
        write_failure(out_dir, clock, exc, mode=args.mode)
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
