"""Create an independent v1.3 ledger by replaying, never copying, v1.2 state.

This migration is research infrastructure only.  It does not switch a Poe
service, a scheduled task, or any order path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path

import poe_ic_im_v1_2_state as old_state
import poe_ic_im_v1_3_state as new_state


ROOT = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def migrate(old_dir: Path, target_dir: Path, *, max_sessions: int = 40) -> dict[str, object]:
    old_dir = old_dir.resolve()
    target_dir = target_dir.resolve()
    if old_dir == target_dir or old_dir in target_dir.parents or target_dir in old_dir.parents:
        raise ValueError("v1.2源账本与v1.3目标账本目录不得相同或相互嵌套")
    if target_dir.exists():
        raise FileExistsError(f"Refusing to overwrite v1.3 ledger: {target_dir}")
    old_store = old_state.StateStore(old_dir)
    old_latest = old_store.load_latest()
    old_day = date.fromisoformat(str(old_latest["verified_day"])[:10])
    staging = target_dir.with_name(f".{target_dir.name}.staging")
    if staging.exists():
        raise FileExistsError(f"Migration staging exists: {staging}")
    previous_state_dir = os.environ.get("ICIM_STATE_DIR")
    os.environ["ICIM_STATE_DIR"] = str(staging)
    try:
        # Import lazily after redirecting the server's module-level bootstrap.
        # A top-level import would initialize the requested target before the
        # migration's overwrite guard can inspect it.
        from poe_ic_im_v1_3_server import LedgerCoordinator
    finally:
        if previous_state_dir is None:
            os.environ.pop("ICIM_STATE_DIR", None)
        else:
            os.environ["ICIM_STATE_DIR"] = previous_state_dir
    store = new_state.StateStore(staging)
    try:
        coordinator = LedgerCoordinator(store)
        genesis = store.load_latest()
        if str(genesis["verified_day"])[:10] > old_day.isoformat():
            raise RuntimeError("v1.3 genesis is later than the v1.2 migration anchor")
        replayed = 0
        while True:
            current = store.load_latest()
            current_day = date.fromisoformat(str(current["verified_day"])[:10])
            if current_day >= old_day:
                break
            if replayed >= max_sessions:
                raise RuntimeError(
                    f"v1.3 replay requires more than max_sessions={max_sessions}"
                )
            next_day = new_state.strategy._roll_forward_exchange_day(
                current_day + timedelta(days=1)
            )
            text, _, observed = coordinator.execute_query(
                "信号",
                new_state.close_clock(next_day),
                replay_day=next_day,
            )
            if set(observed) != set(new_state.PRODUCTS):
                detail = next(
                    (
                        line
                        for line in text.splitlines()
                        if "完整信号失败" in line
                    ),
                    "未返回逐腿失败摘要",
                )
                raise RuntimeError(f"v1.3 replay failed on {next_day}: {detail}")
            replayed += 1
        latest = store.load_latest()
        if str(latest["verified_day"])[:10] != old_day.isoformat():
            raise RuntimeError(
                f"v1.3 replay did not reach v1.2 anchor: {latest['verified_day']} != {old_day}"
            )
        manifest: dict[str, object] = {
            "migration": "ic_im_v1_2_to_v1_3_r5_replay",
            "status": "research_only_not_deployment_switch",
            "old_ledger": {
                "path": str(old_dir),
                "strategy_version": old_latest["strategy_version"],
                "verified_day": old_latest["verified_day"],
                "sequence": old_latest["sequence"],
                "digest": old_latest["digest"],
            },
            "new_ledger": {
                "path": str(target_dir),
                "strategy_version": latest["strategy_version"],
                "strategy_revision": latest["strategy_revision"],
                "genesis_digest": genesis["digest"],
                "verified_day": latest["verified_day"],
                "sequence": latest["sequence"],
                "digest": latest["digest"],
            },
            "replay": {
                "copied_parent_momentum_anchor": False,
                "start_after": genesis["verified_day"],
                "end": latest["verified_day"],
                "sessions": replayed,
                "final_momentum_anchors": {
                    product: {
                        "verified_momentum_weight": latest["products"][product]["verified_momentum_weight"],
                        "verified_next_momentum_weight": latest["products"][product]["verified_next_momentum_weight"],
                    }
                    for product in new_state.PRODUCTS
                },
            },
            "source_sha256": {
                "strategy": sha256(ROOT / "poe_ic_im_mainline_v1_3_bot.py"),
                "state": sha256(ROOT / "poe_ic_im_v1_3_state.py"),
                "server": sha256(ROOT / "poe_ic_im_v1_3_server.py"),
                "migration_runner": sha256(Path(__file__).resolve()),
                "github_digest_runner": sha256(ROOT / "run_ic_im_v1_3_github_digest.py"),
                "im_local_rule": sha256(ROOT / "im_mainline_v1_3.py"),
                "ic_local_rule": sha256(ROOT / "ic_mainline_v1_3.py"),
            },
        }
        payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        manifest["manifest_sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        (staging / "migration_record.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, target_dir)
        return manifest
    except Exception:
        # Preserve a failed staging directory for diagnosis; never publish it as
        # the target ledger and never touch the old v1.2 chain.
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-state-dir", required=True, type=Path)
    parser.add_argument("--new-state-dir", required=True, type=Path)
    parser.add_argument("--max-sessions", type=int, default=40)
    args = parser.parse_args()
    result = migrate(args.old_state_dir, args.new_state_dir, max_sessions=args.max_sessions)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
