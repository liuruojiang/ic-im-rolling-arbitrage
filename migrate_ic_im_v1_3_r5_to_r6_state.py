"""Migrate an audited v1.3-r5 ledger into a new v1.3-r6 ledger.

The r5 chain is read-only.  Migration creates an independent r6 genesis at the
same verified close, retains IC and the IM core/Call/grid/momentum anchors, and
starts the new IM momentum-Put ledger flat.  It never overwrites either path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import poe_ic_im_v1_3_state as new_state


ROOT = Path(__file__).resolve().parent


def _canonical_r5(record: dict[str, Any]) -> bytes:
    payload = {key: value for key, value in record.items() if key != "digest"}
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _validate_r5_chain(source: Path) -> dict[str, Any]:
    latest_path = source / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    if latest.get("schema_version") != 2 or latest.get("strategy_version") != "1.3":
        raise RuntimeError("源账本不是v1.3 schema 2")
    if latest.get("strategy_revision") != "r5":
        raise RuntimeError("源账本不是v1.3-r5")
    previous_digest: str | None = None
    final: dict[str, Any] | None = None
    for sequence in range(int(latest["sequence"]) + 1):
        matches = sorted((source / "journal").glob(f"{sequence:06d}-*.json"))
        if len(matches) != 1:
            raise RuntimeError(f"r5账本序号 {sequence} 不存在或重复")
        record = json.loads(matches[0].read_text(encoding="utf-8"))
        actual = hashlib.sha256(_canonical_r5(record)).hexdigest()
        if record.get("digest") != actual:
            raise RuntimeError(f"r5账本序号 {sequence} SHA-256校验失败")
        if record.get("previous_digest") != previous_digest:
            raise RuntimeError(f"r5账本序号 {sequence} 前序SHA-256链断裂")
        if int(record.get("sequence", -1)) != sequence:
            raise RuntimeError(f"r5账本序号 {sequence} 内容不一致")
        previous_digest = actual
        final = record
    if final is None or final.get("digest") != latest.get("digest"):
        raise RuntimeError("r5 latest.json未指向日志链末端")
    return latest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def migrate(old_dir: Path, target_dir: Path) -> dict[str, Any]:
    old_dir = old_dir.resolve()
    target_dir = target_dir.resolve()
    if old_dir == target_dir or old_dir in target_dir.parents or target_dir in old_dir.parents:
        raise ValueError("r5源账本与r6目标账本目录不得相同或相互嵌套")
    if target_dir.exists():
        raise FileExistsError(f"Refusing to overwrite r6 ledger: {target_dir}")
    old_latest = _validate_r5_chain(old_dir)
    products = deepcopy(old_latest["products"])
    im = products["IM"]
    core_qty = float(im["verified_put_qty_normalized"])
    core_contract = im.get("post_put_contract")
    im.update(
        {
            "post_core_put_contract": core_contract,
            "post_momentum_put_contract": None,
            "post_core_put_equivalent_units": float(
                im.get("post_put_equivalent_units", 0.5 * core_qty)
            ),
            "post_momentum_put_equivalent_units": 0.0,
            "verified_core_put_qty_normalized": core_qty,
            "verified_momentum_put_qty_normalized": 0.0,
            "verified_total_put_qty_normalized": core_qty,
            "verified_put_qty_normalized": core_qty,
        }
    )
    record: dict[str, Any] = {
        "schema_version": new_state.SCHEMA_VERSION,
        "strategy_version": new_state.STRATEGY_VERSION,
        "strategy_revision": new_state.STRATEGY_REVISION,
        "sequence": 0,
        "verified_day": old_latest["verified_day"],
        "updated_at": datetime.now(new_state.strategy.BEIJING).isoformat(),
        "previous_digest": None,
        "products": products,
        "signals": {},
        "source": "migration_from_v1_3_r5",
        "genesis": {
            "parent_revision": "r5",
            "parent_sequence": old_latest["sequence"],
            "parent_digest": old_latest["digest"],
            "im_momentum_put_started_flat": True,
        },
    }
    record["digest"] = new_state._digest(record)
    new_state._validate_record(record)
    staging = target_dir.with_name(f".{target_dir.name}.staging")
    if staging.exists():
        raise FileExistsError(f"Migration staging exists: {staging}")
    store = new_state.StateStore(staging)
    try:
        store._atomic_write(
            store.journal_dir / f"000000-{record['verified_day']}.json", record
        )
        store._atomic_write(store.latest_path, record)
        checked = store.load_latest()
        manifest: dict[str, Any] = {
            "migration": "ic_im_v1_3_r5_to_r6",
            "status": "research_signal_publication_not_order_authorization",
            "old_ledger": {
                "path": str(old_dir),
                "sequence": old_latest["sequence"],
                "verified_day": old_latest["verified_day"],
                "digest": old_latest["digest"],
            },
            "new_ledger": {
                "path": str(target_dir),
                "sequence": checked["sequence"],
                "verified_day": checked["verified_day"],
                "digest": checked["digest"],
                "strategy_revision": checked["strategy_revision"],
            },
            "source_sha256": {
                "state": _sha256(ROOT / "poe_ic_im_v1_3_state.py"),
                "strategy": _sha256(ROOT / "poe_ic_im_mainline_v1_3_bot.py"),
                "migration_runner": _sha256(Path(__file__).resolve()),
            },
        }
        payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        manifest["manifest_sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        store._atomic_write(staging / "migration_record.json", manifest)
        os.replace(staging, target_dir)
        return manifest
    except Exception:
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-state-dir", required=True, type=Path)
    parser.add_argument("--new-state-dir", required=True, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            migrate(args.old_state_dir, args.new_state_dir),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
