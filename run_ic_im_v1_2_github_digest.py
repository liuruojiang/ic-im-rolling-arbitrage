"""Build one close-confirmed IC/IM v1.2 signal artifact for GitHub Actions.

The runner owns no scheduler and sends no email.  It restores/updates the
hash-chained ledger through :class:`StateStore`, verifies that the generated
report is identical to the just-committed ledger record, and writes portable
JSON/Markdown artifacts for the separate automation repository.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import poe_ic_im_mainline_v1_2_bot as strategy
from poe_ic_im_v1_2_server import LedgerCoordinator
from poe_ic_im_v1_2_state import StateStore, _jsonable


PRODUCTS = ("IC", "IM")


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
        if not bool(signal.get("close_confirmed")):
            raise RuntimeError(f"{product}不是收盘确认信号")
        if str(signal.get("product")) != product:
            raise RuntimeError(f"{product}信号产品标签不一致")
    if str(latest.get("verified_day"))[:10] != completed_day.isoformat():
        raise RuntimeError("持久账本尚未推进到最近完成交易日")
    if _jsonable(observed) != latest.get("signals"):
        raise RuntimeError("邮件信号与持久账本最新逐腿记录不一致")


def build_artifacts(
    *, state_dir: Path, out_dir: Path, clock: datetime, max_sessions: int
) -> dict[str, Any]:
    store = StateStore(state_dir)
    coordinator = LedgerCoordinator(store)
    completed_day = strategy._latest_completed_exchange_day(clock)
    advanced = coordinator.catch_up_until_current(clock, max_sessions=max_sessions)
    health = coordinator.health()
    if health.get("status") != "ok":
        raise RuntimeError(str(health.get("refresh_error") or "账本补写状态异常"))
    if str(health.get("verified_day"))[:10] != completed_day.isoformat():
        raise RuntimeError(
            "账本未追平最近完成交易日："
            f"verified={health.get('verified_day')} completed={completed_day}"
        )

    report, attachments, observed = coordinator.execute_query(
        "信号",
        clock,
        persist_confirmed=False,
        replay_day=completed_day,
    )
    latest = store.load_latest()
    validate_close_artifact(
        completed_day=completed_day,
        latest=latest,
        observed=observed,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "ic_im_v1_2_close_signal.md"
    report_path.write_text(report, encoding="utf-8")
    result = {
        "status": "ok",
        "strategy": "IC/IM research candidate 1.2",
        "build": strategy.BUILD_ID,
        "generated_at": clock.isoformat(),
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
    (out_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def write_failure(out_dir: Path, clock: datetime, exc: Exception) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "failed",
        "strategy": "IC/IM research candidate 1.2",
        "build": strategy.BUILD_ID,
        "generated_at": clock.isoformat(),
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
    (out_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "failure.txt").write_text(
        f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--now", default="")
    parser.add_argument("--max-sessions", type=int, default=20)
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
        )
    except Exception as exc:
        write_failure(out_dir, clock, exc)
        print(f"ic_im_digest_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        "ic_im_digest_ok: "
        f"verified_day={result['verified_day']} sequence={result['sequence']} "
        f"build={result['build']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
