from __future__ import annotations

import argparse
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


BJ = ZoneInfo("Asia/Shanghai")
CONTINUOUS_TRADING_WINDOWS = (
    (time(9, 30), time(11, 30)),
    (time(13, 0), time(15, 0)),
)


def is_continuous_trading_time(value: datetime) -> bool:
    local = value.astimezone(BJ)
    if local.weekday() >= 5:
        return False
    current = local.time().replace(tzinfo=None)
    return any(start <= current <= end for start, end in CONTINUOUS_TRADING_WINDOWS)


def resolve_mode(requested: str, value: datetime) -> tuple[str, str]:
    if requested not in {"auto", "realtime", "close_confirmed"}:
        raise ValueError(f"unsupported publication mode: {requested}")
    if requested != "auto":
        return requested, "manual_request"
    if is_continuous_trading_time(value):
        return "realtime", "scheduled_run_inside_continuous_session"
    return "close_confirmed", "scheduled_run_outside_continuous_session"


def write_outputs(values: dict[str, str]) -> None:
    rendered = "".join(f"{key}={value}\n" for key, value in values.items())
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(rendered)
    else:
        print(rendered, end="")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--requested",
        choices=("auto", "realtime", "close_confirmed"),
        default="auto",
    )
    args = parser.parse_args()

    now = datetime.now(BJ)
    mode, basis = resolve_mode(args.requested, now)
    write_outputs(
        {
            "mode": mode,
            "runner_mode": "close" if mode == "close_confirmed" else "realtime",
            "basis": basis,
            "beijing_time": now.isoformat(timespec="seconds"),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
