"""Second dedupe gate using the signal's actual market date, before SMTP intent."""
from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from check_ic_im_v1_4_delivery import (
    fetch_artifacts,
    marker_exists,
    marker_prefix,
    pending_send_exists,
    write_outputs,
)
from prepare_ic_im_v1_4_marker import marker_name


def should_send_for_result(result: dict[str, object], artifacts: dict[str, object], *, correction: bool) -> bool:
    marker_name(result)  # Validate version, mode, date and digest before matching artifacts.
    if correction:
        return True
    prefix = marker_prefix(date.fromisoformat(str(result["market_date"])), str(result["publication_mode"]))
    if marker_exists(artifacts, prefix):
        return False
    if pending_send_exists(artifacts, prefix):
        raise RuntimeError(
            "BLOCKED: prior signal-day SMTP send intent has no completion marker; delivery is uncertain"
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    parser.add_argument("--correction", action="store_true")
    args = parser.parse_args()
    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    if not args.correction and (not args.repository or not args.token):
        raise SystemExit("GITHUB_REPOSITORY and GITHUB_TOKEN are required")
    artifacts = {} if args.correction else fetch_artifacts(args.repository, args.token, args.api_url)
    should_send = should_send_for_result(result, artifacts, correction=args.correction)
    write_outputs({"should_send_result_day": str(should_send).lower()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

