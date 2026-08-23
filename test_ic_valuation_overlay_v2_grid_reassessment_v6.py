import hashlib

import ic_valuation_overlay_v2_grid_reassessment_v6 as scan


def test_preregistered_grid_shape_and_baseline() -> None:
    candidates = scan.grid()
    assert len(candidates) == 62
    assert scan.CURRENT_PAIR in candidates
    assert len(set(candidates)) == len(candidates)
    assert all(high - low >= scan.MIN_GAP - 1e-12 for low, high in candidates)


def test_spec_hash_and_labels() -> None:
    assert hashlib.sha256(scan.SPEC.read_bytes()).hexdigest() == scan.SPEC_SHA256
    assert scan.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == scan.SPEC_SHA256
    assert scan.label(*scan.CURRENT_PAIR) == "L0.375_H1.000"
