import json

import poe_ic_im_v1_2_state as old_state
from migrate_ic_im_v1_2_to_v1_3_state import migrate


def test_migration_replays_into_independent_v13_ledger_without_touching_v12(tmp_path):
    old_dir = tmp_path / "v12"
    new_dir = tmp_path / "v13"
    old_store = old_state.StateStore(old_dir)
    old_record = old_store.initialize()
    old_bytes = old_store.latest_path.read_bytes()

    manifest = migrate(old_dir, new_dir)

    assert old_store.latest_path.read_bytes() == old_bytes
    assert manifest["status"] == "research_only_not_deployment_switch"
    assert manifest["old_ledger"]["digest"] == old_record["digest"]
    assert manifest["new_ledger"]["strategy_version"] == "1.3"
    assert manifest["new_ledger"]["strategy_revision"] == "r5"
    assert manifest["replay"]["copied_parent_momentum_anchor"] is False
    migrated = json.loads((new_dir / "latest.json").read_text(encoding="utf-8"))
    assert migrated["strategy_version"] == "1.3"
    assert migrated["strategy_revision"] == "r5"
    assert (new_dir / "migration_record.json").is_file()
