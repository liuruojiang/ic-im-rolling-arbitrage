from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


BJ = ZoneInfo("Asia/Shanghai")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def delivery_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BJ).date()


REVISION = "r1"
EXPECTED_BUILD = "v1.4-20260928-r1-coreput3x-open-fix7-nocall-repeatroll-iciv30-qdelta05"
EXPECTED_DELIVERY_REVISION = "20260928-v14-coreput3x-open-fix7-nocall-repeatroll-iciv30-qdelta05"
FIX8_BUILD = "v1.4-20260929-r1-ordinaryput-open-fix8"
FIX8_DELIVERY_REVISION = "20260929-v14-ordinary-put-open-fix8"
FIX9_BUILD = "v1.4-20260929-r1-ordinaryput-open-ic-seller-mom120-fix9"
FIX9_DELIVERY_REVISION = "20260929-v14-ordinary-put-open-ic-seller-mom120-fix9"
FIX10_BUILD = "v1.4-20261008-r1-fear-grid25-50-fix10"
FIX10_DELIVERY_REVISION = "20261008-v14-fear-grid25-50-fix10"
FIX11_BUILD = "v1.4-20261008-r1-ic-csi500-abs40-fix11"
FIX11_DELIVERY_REVISION = "20261008-v14-ic-csi500-abs40-fix11"
FIX6_BUILD = "v1.4-20260926-r1-coreput3x-fixedshort95-fix6-nocall-repeatroll-iciv30-qdelta05"
FIX6_DELIVERY_REVISION = "20260926-v14-coreput3x-fixedshort95-fix6-nocall-repeatroll-iciv30-qdelta05"
LEGACY_BUILD = "v1.4-20260924-r1-coreput3x-fixedshort95-fix4-integrated-iciv30-qdelta05"
LEGACY_DELIVERY_REVISION = "20260924-v14-coreput3x-fixedshort95-fix4-integrated-iciv30-qdelta05"
REPEAT_ROLL_EFFECTIVE_DATE = date(2026, 9, 26)
PROFIT_OPEN_EFFECTIVE_DATE = date(2026, 9, 28)
ORDINARY_PUT_OPEN_EFFECTIVE_DATE = date(2026, 9, 29)
FEAR_GRID_EFFECTIVE_DATE = date(2026, 10, 8)
IC_CSI500_EFFECTIVE_DATE = date(2026, 10, 8)
IDENTITY_TAG = hashlib.sha256(
    f"{EXPECTED_BUILD}\n{EXPECTED_DELIVERY_REVISION}".encode("utf-8")
).hexdigest()[:12]
LEGACY_IDENTITY_TAG = hashlib.sha256(
    f"{LEGACY_BUILD}\n{LEGACY_DELIVERY_REVISION}".encode("utf-8")
).hexdigest()[:12]
FIX6_IDENTITY_TAG = hashlib.sha256(
    f"{FIX6_BUILD}\n{FIX6_DELIVERY_REVISION}".encode("utf-8")
).hexdigest()[:12]
FIX8_IDENTITY_TAG = hashlib.sha256(
    f"{FIX8_BUILD}\n{FIX8_DELIVERY_REVISION}".encode("utf-8")
).hexdigest()[:12]
FIX9_IDENTITY_TAG = hashlib.sha256(
    f"{FIX9_BUILD}\n{FIX9_DELIVERY_REVISION}".encode("utf-8")
).hexdigest()[:12]
FIX10_IDENTITY_TAG = hashlib.sha256(
    f"{FIX10_BUILD}\n{FIX10_DELIVERY_REVISION}".encode("utf-8")
).hexdigest()[:12]
FIX11_IDENTITY_TAG = hashlib.sha256(
    f"{FIX11_BUILD}\n{FIX11_DELIVERY_REVISION}".encode("utf-8")
).hexdigest()[:12]


def expected_identity_for_day(value: date) -> tuple[str, str]:
    if value >= IC_CSI500_EFFECTIVE_DATE:
        return FIX11_BUILD, FIX11_DELIVERY_REVISION
    if value >= FEAR_GRID_EFFECTIVE_DATE:
        return FIX10_BUILD, FIX10_DELIVERY_REVISION
    if value >= ORDINARY_PUT_OPEN_EFFECTIVE_DATE:
        return FIX9_BUILD, FIX9_DELIVERY_REVISION
    if value >= PROFIT_OPEN_EFFECTIVE_DATE:
        return EXPECTED_BUILD, EXPECTED_DELIVERY_REVISION
    if value >= REPEAT_ROLL_EFFECTIVE_DATE:
        return FIX6_BUILD, FIX6_DELIVERY_REVISION
    return LEGACY_BUILD, LEGACY_DELIVERY_REVISION


def marker_prefix(value: date, publication_mode: str) -> str:
    mode = publication_mode.strip().lower()
    if mode not in {"realtime", "close_confirmed"}:
        raise ValueError(f"unsupported publication mode: {publication_mode}")
    tag = (LEGACY_IDENTITY_TAG if value < REPEAT_ROLL_EFFECTIVE_DATE else
           FIX6_IDENTITY_TAG if value < PROFIT_OPEN_EFFECTIVE_DATE else
           IDENTITY_TAG if value < ORDINARY_PUT_OPEN_EFFECTIVE_DATE else
           FIX9_IDENTITY_TAG if value < FEAR_GRID_EFFECTIVE_DATE else
           FIX10_IDENTITY_TAG if value < IC_CSI500_EFFECTIVE_DATE else FIX11_IDENTITY_TAG)
    return (
        f"ic-im-v1-4-{REVISION}-{tag}-{mode}-digest-delivered-"
        f"{value.isoformat()}-"
    )


def marker_exists(payload: dict[str, object], prefix: str) -> bool:
    artifacts = payload.get("artifacts", [])
    return isinstance(artifacts, list) and any(
        isinstance(item, dict)
        and str(item.get("name", "")).startswith(prefix)
        and not str(item.get("name", "")).endswith("-send-intent")
        and item.get("expired") is not True
        for item in artifacts
    )


def pending_send_exists(payload: dict[str, object], prefix: str) -> bool:
    return any(isinstance(item, dict) and str(item.get("name", "")).startswith(prefix)
               and str(item.get("name", "")).endswith("-send-intent")
               and item.get("expired") is not True for item in payload.get("artifacts", []))


def fetch_artifacts(repository: str, token: str, api_url: str) -> dict[str, object]:
    artifacts: list[object] = []
    for page in range(1, 101):
        payload = fetch_artifact_page(repository, token, api_url, page)
        items = payload.get("artifacts")
        if not isinstance(items, list):
            raise RuntimeError("GitHub artifacts response must contain an artifacts list")
        artifacts.extend(items)
        if len(items) < 100:
            return {"artifacts": artifacts}
    raise RuntimeError("GitHub artifacts pagination exceeded safety limit; refusing uncertain dedupe")


def fetch_artifact_page(repository: str, token: str, api_url: str, page: int) -> dict[str, object]:
    url = f"{api_url.rstrip('/')}/repos/{repository}/actions/artifacts?per_page=100"
    url += f"&page={page}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ic-im-delivery-gate",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub artifacts response must be an object")
    return payload


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
    parser.add_argument("--correction", action="store_true")
    parser.add_argument(
        "--publication-mode",
        choices=("realtime", "close_confirmed"),
        default="realtime",
    )
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    args = parser.parse_args()

    day = delivery_date(now_utc())
    prefix = marker_prefix(day, args.publication_mode)
    exists = False
    if not args.correction:
        if not args.repository or not args.token:
            raise SystemExit("GITHUB_REPOSITORY and GITHUB_TOKEN are required")
        artifacts = fetch_artifacts(args.repository, args.token, args.api_url)
        exists = marker_exists(artifacts, prefix)
        if not exists and pending_send_exists(artifacts, prefix):
            raise RuntimeError("BLOCKED: prior SMTP send intent has no completion marker; delivery is uncertain, reconcile Gmail before explicit correction")
    write_outputs(
        {
            "should_send": str(args.correction or not exists).lower(),
            "marker_prefix": prefix,
            "subject_prefix": "纠正版" if args.correction else "",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
