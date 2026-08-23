import hashlib

import im_grid160_put_carry_objective_scan_v23 as scan


def test_preregistered_candidate_bundle() -> None:
    names = [item["candidate"] for item in scan.CANDIDATES]
    assert len(names) == 8
    assert len(set(names)) == 8
    assert scan.NO_PUT in names
    assert scan.CURRENT in names
    assert {item["mom_floor"] for item in scan.CANDIDATES if item["valuation_family"] == "current_4tier"} == set(range(5))


def test_spec_hash() -> None:
    assert hashlib.sha256(scan.SPEC.read_bytes()).hexdigest() == scan.SPEC_SHA256
    assert scan.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == scan.SPEC_SHA256
