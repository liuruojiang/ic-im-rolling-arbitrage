from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import poe_ic_im_mainline_v1_3_bot as strategy
import poe_ic_im_v1_3_state as state
from migrate_ic_im_v1_3_r5_to_r6_state import migrate


def _write_r5_fixture(root):
    products = state._jsonable(deepcopy(strategy.LIVE_CONTINUATION_ANCHOR))
    im = products["IM"]
    for key in (
        "post_core_put_contract",
        "post_momentum_put_contract",
        "post_core_put_equivalent_units",
        "post_momentum_put_equivalent_units",
        "verified_core_put_qty_normalized",
        "verified_momentum_put_qty_normalized",
        "verified_total_put_qty_normalized",
    ):
        im.pop(key, None)
    record = {
        "schema_version": 2,
        "strategy_version": "1.3",
        "strategy_revision": "r5",
        "sequence": 0,
        "verified_day": products["IC"]["last_verified_day"],
        "updated_at": "2026-09-02T16:00:00+08:00",
        "previous_digest": None,
        "products": products,
        "signals": {},
        "source": "test_fixture",
    }
    raw = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    record["digest"] = hashlib.sha256(raw).hexdigest()
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    (root / "journal").mkdir(parents=True)
    (root / "journal" / f"000000-{record['verified_day']}.json").write_text(
        payload, encoding="utf-8"
    )
    (root / "latest.json").write_text(payload, encoding="utf-8")
    return record


def test_r5_to_r6_migration_is_independent_and_starts_momentum_put_flat(tmp_path):
    source = tmp_path / "r5"
    target = tmp_path / "r6"
    old = _write_r5_fixture(source)
    result = migrate(source, target)
    latest = state.StateStore(target).load_latest()
    assert result["old_ledger"]["digest"] == old["digest"]
    assert latest["schema_version"] == 3
    assert latest["strategy_revision"] == "r6"
    assert latest["verified_day"] == old["verified_day"]
    assert latest["products"]["IC"] == old["products"]["IC"]
    im = latest["products"]["IM"]
    assert im["post_core_put_contract"] == old["products"]["IM"]["post_put_contract"]
    assert im["post_momentum_put_contract"] is None
    assert im["verified_core_put_qty_normalized"] == 1.5
    assert im["verified_momentum_put_qty_normalized"] == 0.0
    assert im["verified_total_put_qty_normalized"] == 1.5


def test_migration_refuses_overwrite_and_tampered_r5(tmp_path):
    source = tmp_path / "r5"
    target = tmp_path / "r6"
    _write_r5_fixture(source)
    target.mkdir()
    try:
        migrate(source, target)
    except FileExistsError:
        pass
    else:
        raise AssertionError("migration must refuse overwrite")
    target.rmdir()
    journal = next((source / "journal").glob("*.json"))
    record = json.loads(journal.read_text(encoding="utf-8"))
    record["products"]["IM"]["verified_put_qty_normalized"] = 999
    journal.write_text(json.dumps(record), encoding="utf-8")
    try:
        migrate(source, target)
    except RuntimeError as exc:
        assert "SHA-256" in str(exc)
    else:
        raise AssertionError("migration must reject tampered r5 chain")
