"""One-way migration from the verified v1.3-r7 ledger to v1.4-r1."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as strategy
import poe_ic_im_v1_3_state as old_state
import poe_ic_im_v1_4_state as new_state


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "runtime" / "ic_im_v1_3_r7"
DEFAULT_TARGET = ROOT / "runtime" / "ic_im_v1_4_r1"


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(new_state._jsonable(payload), stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_migration(source_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source_store = old_state.StateStore(source_root)
    parent = source_store.load_latest()
    old_state._validate_record(parent)
    products = deepcopy(parent["products"])
    parent_anchors = old_state.anchors_from_record(parent)
    ic_quantities = strategy._ic_current_quantity_breakdown(parent_anchors["IC"])
    products["IC"]["verified_core_put_qty"] = ic_quantities["core"]
    products["IC"]["verified_momentum_put_qty"] = ic_quantities["momentum"]
    for product in new_state.PRODUCTS:
        products[product].update(policy.migrated_extension(product))
    if ic_quantities["core"] > 0:
        products["IC"].update(
            v14_core_put_contract=parent_anchors["IC"]["post_put_contract"],
            v14_core_put_security_id=parent_anchors["IC"].get("post_put_security_id"),
            v14_core_put_qty=ic_quantities["core"],
        )
    record: dict[str, Any] = {
        "schema_version": new_state.SCHEMA_VERSION,
        "strategy_version": new_state.STRATEGY_VERSION,
        "strategy_revision": new_state.STRATEGY_REVISION,
        "sequence": 0,
        "verified_day": parent["verified_day"],
        "updated_at": datetime.now(strategy.BEIJING).isoformat(),
        "previous_digest": None,
        "products": products,
        "signals": {},
        "source": "one_way_v1_3_r7_to_v1_4_r1_migration",
        "genesis": {
            "parent_strategy_version": old_state.STRATEGY_VERSION,
            "parent_strategy_revision": old_state.STRATEGY_REVISION,
            "parent_sequence": parent["sequence"],
            "parent_verified_day": parent["verified_day"],
            "parent_digest": parent["digest"],
            "build": strategy.BUILD_ID,
            "rule_revision": policy.RULE_REVISION,
            "effective_signal_date": policy.EFFECTIVE_SIGNAL_DATE,
            "core_put_3x_migration_policy": (
                "existing Put is ineligible because the original entry premium is not "
                "uniquely persisted; eligibility begins after the next ordinary reset"
            ),
            "short_put_initial_state": "future/no short Put",
        },
    }
    record["digest"] = new_state._digest(record)
    new_state._validate_record(record)
    migration = {
        "created_at": datetime.now(strategy.BEIJING).isoformat(),
        "migration": "v1.3-r7 -> v1.4-r1",
        "source_ledger": {
            "root": str(source_root),
            "latest_sha256": _file_sha(source_store.latest_path),
            "strategy_version": parent["strategy_version"],
            "strategy_revision": parent["strategy_revision"],
            "sequence": parent["sequence"],
            "verified_day": parent["verified_day"],
            "digest": parent["digest"],
        },
        "new_ledger": {
            "strategy_version": record["strategy_version"],
            "strategy_revision": record["strategy_revision"],
            "schema_version": record["schema_version"],
            "sequence": record["sequence"],
            "verified_day": record["verified_day"],
            "digest": record["digest"],
        },
        "production_or_external_actions": False,
    }
    return record, migration


def migrate(source_root: Path, target_root: Path) -> dict[str, Any]:
    if target_root.resolve() == source_root.resolve():
        raise RuntimeError("v1.4 target must be independent from the v1.3 ledger")
    if target_root.exists() and any(target_root.iterdir()):
        raise RuntimeError("v1.4 target is not empty; refusing to overwrite")
    record, migration = build_migration(source_root)
    target_root.mkdir(parents=True, exist_ok=True)
    journal = target_root / "journal"
    journal.mkdir(exist_ok=False)
    journal_path = journal / f"000000-{record['verified_day']}.json"
    _atomic_json(journal_path, record)
    _atomic_json(target_root / "latest.json", record)
    _atomic_json(target_root / "migration_record.json", migration)
    store = new_state.StateStore(target_root)
    loaded = store.load_latest()
    new_state.anchors_from_record(loaded)
    if loaded["digest"] != record["digest"]:
        raise RuntimeError("v1.4 migration verification failed")
    return migration


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        record, migration = build_migration(args.source)
        print(json.dumps({"record": record, "migration": migration}, ensure_ascii=False, indent=2, default=str))
    else:
        result = migrate(args.source, args.target)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
