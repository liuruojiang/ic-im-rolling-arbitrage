"""Create an independent forward-only r7 chain without changing any r6 anchor."""
from __future__ import annotations

import argparse
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import poe_ic_im_v1_3_state as state


def validate_r6(source):
    latest = json.loads((source / "latest.json").read_text(encoding="utf-8"))
    if state._digest(latest) != latest.get("digest"):
        raise RuntimeError("r6 latest digest mismatch")
    previous = None
    previous_day = None
    for sequence in range(int(latest["sequence"])+1):
        matches = list((source/"journal").glob(f"{sequence:06d}-*.json"))
        if len(matches) != 1:
            raise RuntimeError("missing or duplicate r6 journal")
        item = json.loads(matches[0].read_text(encoding="utf-8"))
        if item.get("strategy_revision") != "r6" or item.get("schema_version") != 3:
            raise RuntimeError("source must be r6 schema 3")
        if item.get("sequence") != sequence or item.get("digest") != state._digest(item):
            raise RuntimeError("r6 sequence or hash mismatch")
        if item.get("previous_digest") != previous:
            raise RuntimeError("r6 hash chain broken")
        day = state._as_day(item["verified_day"], "r6 day")
        if matches[0].name != f"{sequence:06d}-{day}.json":
            raise RuntimeError("r6 filename mismatch")
        if previous_day is not None and day != state.strategy._roll_forward_exchange_day(previous_day+timedelta(days=1)):
            raise RuntimeError("r6 chain skipped a trading day")
        check = deepcopy(item)
        check["strategy_revision"] = state.STRATEGY_REVISION
        check["digest"] = state._digest(check)
        state._validate_record(check)
        previous, previous_day = item["digest"], day
    if previous != latest["digest"]:
        raise RuntimeError("r6 latest is not journal tail")
    return latest


def migrate(old_dir, new_dir):
    source, target = old_dir.resolve(), new_dir.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("source and target must be disjoint")
    if target.exists():
        raise FileExistsError(target)
    old = validate_r6(source)
    record = dict(schema_version=state.SCHEMA_VERSION, strategy_version="1.3",
                  strategy_revision=state.STRATEGY_REVISION, sequence=0,
                  verified_day=old["verified_day"], previous_digest=None,
                  products=deepcopy(old["products"]), signals={},
                  updated_at=datetime.now(state.strategy.BEIJING).isoformat(),
                  source="migration_from_v1_3_r6",
                  genesis=dict(parent_revision="r6", parent_digest=old["digest"],
                               parent_sequence=old["sequence"], anchors_unchanged=True,
                               policy_effective_date="2026-09-04"))
    record["digest"] = state._digest(record)
    state._validate_record(record)
    staging = target.with_name("."+target.name+".staging")
    if staging.exists():
        raise FileExistsError(staging)
    store = state.StateStore(staging)
    store._atomic_write(store.journal_dir/f"000000-{record['verified_day']}.json", record)
    store._atomic_write(store.latest_path, record)
    checked = store.load_latest()
    if checked["products"] != old["products"]:
        raise RuntimeError("migration changed anchors")
    manifest = dict(migration="ic_im_v1_3_r6_to_r7", anchors_unchanged=True,
                    old_ledger={k: old[k] for k in ("sequence", "verified_day", "digest", "strategy_revision")},
                    new_ledger={k: checked[k] for k in ("sequence", "verified_day", "digest", "strategy_revision")})
    store._atomic_write(staging/"migration_record.json", manifest)
    os.replace(staging, target)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-state-dir", required=True, type=Path)
    parser.add_argument("--new-state-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.old_state_dir, args.new_state_dir), ensure_ascii=False, indent=2))
