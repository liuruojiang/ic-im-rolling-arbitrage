from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date
from pathlib import Path


EXPECTED_REVISION = "r7"


def marker_name(payload: dict[str, object]) -> str:
    if payload.get("status") != "ok":
        raise ValueError("delivery marker requires a successful signal result")
    if str(payload.get("strategy_revision")) != EXPECTED_REVISION:
        raise ValueError("delivery marker requires strategy_revision=r7")
    publication_mode = str(payload.get("publication_mode", ""))
    if publication_mode not in {"realtime", "close_confirmed"}:
        raise ValueError("delivery marker has unsupported publication_mode")
    market_date = str(payload.get("market_date", ""))
    digest = str(payload.get("digest", ""))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", market_date) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("delivery marker requires market_date and full SHA-256 digest")
    date.fromisoformat(market_date)
    return (
        f"ic-im-v1-3-{EXPECTED_REVISION}-{publication_mode}-digest-delivered-"
        f"{market_date}-{digest[:12]}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--marker-dir", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.result).read_text(encoding="utf-8"))
    name = marker_name(payload)
    marker_dir = Path(args.marker_dir)
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "delivery.json").write_text(
        json.dumps(
            {
                "strategy_revision": payload["strategy_revision"],
                "publication_mode": payload["publication_mode"],
                "market_date": payload["market_date"],
                "digest": payload["digest"],
                "github_run_url": os.environ.get("GITHUB_RUN_URL", ""),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    rendered = f"marker_name={name}\n"
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
