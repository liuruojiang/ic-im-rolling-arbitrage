"""Publish only an agent-verified daily webpage observation, never credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import date, datetime
from pathlib import Path

import ic_im_daily_valuation as valuation
from run_ic_im_v1_3_github_digest import _atomic_write_text

REPOSITORY = "liuruojiang/codex-daily-automation-probe"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Verified webpage observations JSON")
    parser.add_argument("--evidence", required=True, help="Local captured page evidence")
    parser.add_argument("--expected-date", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    observed = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    if not isinstance(observed, dict) or set(observed) != {"valuation_date", "indices", "source_urls"}:
        raise ValueError("网页观察文件只允许日期、两指数四项估值和四个来源URL")
    if observed["valuation_date"] != args.expected_date:
        raise ValueError("网页日期不匹配，拒绝上传")
    now = datetime.now(valuation.BEIJING)
    from poe_ic_im_mainline_v1_3_bot import _is_exchange_trading_day, _latest_completed_exchange_day
    day = date.fromisoformat(args.expected_date)
    if not _is_exchange_trading_day(day) or day > _latest_completed_exchange_day(now):
        raise ValueError("数据日不是已完成交易日")
    evidence = Path(args.evidence).read_bytes()
    if not evidence:
        raise ValueError("网页证据为空")
    snapshot = valuation.validate_snapshot({
        "schema_version": 1, **observed, "fetched_at": now.isoformat(),
        "evidence_sha256": hashlib.sha256(evidence).hexdigest(),
    })
    content_hash = hashlib.sha256(json.dumps(observed, sort_keys=True).encode()).hexdigest()
    root = Path(args.out_dir)
    marker = root / "upload.json"
    endpoint = f"repos/{REPOSITORY}/actions/secrets/{valuation.ENV}"

    def remote_metadata() -> dict:
        command = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True, timeout=30)
        if command.returncode:
            return {}
        return json.loads(command.stdout)

    if args.upload and marker.is_file():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        if previous.get("content_sha256") == content_hash:
            remote = remote_metadata()
            if remote.get("updated_at") and remote.get("updated_at") == previous.get("remote_updated_at"):
                print("already_uploaded: verified unchanged daily observation")
                return 0
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    target = root / f"snapshot-{content_hash[:12]}.json"
    _atomic_write_text(target, raw + "\n")
    _atomic_write_text(root / "snapshot.json", raw + "\n")
    if args.upload:
        command = subprocess.run(
            ["gh", "secret", "set", valuation.ENV, "--repo", REPOSITORY],
            input=raw, text=True, encoding="utf-8", capture_output=True, timeout=60,
        )
        if command.returncode:
            raise RuntimeError("GitHub Secret上传未确认；先核查远端状态，不自动重试")
        remote = remote_metadata()
        if remote.get("name") != valuation.ENV or not remote.get("updated_at"):
            raise RuntimeError("上传后远端更新时间未确认；请核查，禁止盲目重试")
        _atomic_write_text(marker, json.dumps({
            "valuation_date": day.isoformat(), "content_sha256": content_hash,
            "snapshot_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "remote_updated_at": remote["updated_at"], "repository": REPOSITORY,
        }, ensure_ascii=False, indent=2) + "\n")
        # Local users can explicitly opt into this same, date-checked snapshot.
        _atomic_write_text(Path(__file__).parent / "runtime/legulegu/latest.json", raw + "\n")
    print(f"valuation_sync_ok: date={day} uploaded={args.upload}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
