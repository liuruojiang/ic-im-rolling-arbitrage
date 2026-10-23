from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date
from pathlib import Path

from check_ic_im_v1_4_delivery import (
    REVISION as EXPECTED_REVISION,
    expected_identity_for_day,
    marker_prefix,
)


def marker_name(payload: dict[str, object]) -> str:
    from ic_im_v1_4_delivery_contract import validate_fear_publication
    validate_fear_publication(payload)
    if payload.get("status") not in {"ok", "partial"}:
        raise ValueError("delivery marker requires a successful signal result")
    if payload.get("status") == "partial" and (
        payload.get("signal_completeness") != "partial" or not payload.get("incomplete_components")
        or not any(s.get("grid_action") == "UNCONFIRMED" and s.get("fear_source_policy_revision") == "baifenwei_either_source_20261016_v1"
                   for s in payload.get("signals", {}).values())):
        raise ValueError("partial delivery requires explicit bounded Fear warning")
    if str(payload.get("strategy_revision")) != EXPECTED_REVISION:
        raise ValueError("delivery marker requires strategy_revision=r1")
    market_date = str(payload.get("market_date", ""))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", market_date):
        raise ValueError("delivery marker requires ISO market_date")
    parsed_market_date = date.fromisoformat(market_date)
    expected_build, expected_delivery = expected_identity_for_day(parsed_market_date)
    if str(payload.get("build")) != expected_build:
        raise ValueError("delivery marker build does not match signal-day identity")
    if str(payload.get("delivery_revision", "")) != expected_delivery:
        raise ValueError("delivery marker revision does not match signal-day identity")
    publication_mode = str(payload.get("publication_mode", ""))
    if publication_mode not in {"realtime", "close_confirmed"}:
        raise ValueError("delivery marker has unsupported publication_mode")
    digest = str(payload.get("digest", ""))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", market_date) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("delivery marker requires market_date and full SHA-256 digest")
    return f"{marker_prefix(parsed_market_date, publication_mode)}{digest[:12]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--marker-dir", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.result).read_text(encoding="utf-8"))
    # The naming helper is also used inside this validator.  Keep the import
    # at the write boundary so standalone CLI use gets the full contract
    # without introducing a module-level circular dependency.
    from build_ic_im_v1_4_digest import validate_success_payload
    validate_success_payload(payload)
    name = marker_name(payload)
    marker_dir = Path(args.marker_dir)
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "delivery.json").write_text(
        json.dumps(
            {
                "strategy_revision": payload["strategy_revision"],
                "build": payload["build"],
                "delivery_revision": payload["delivery_revision"],
                "publication_mode": payload["publication_mode"],
                "market_date": payload["market_date"],
                "digest": payload["digest"],
                "signal_completeness": payload.get("signal_completeness", "complete"),
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

