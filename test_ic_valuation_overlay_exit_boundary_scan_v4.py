import ic_valuation_overlay_exit_boundary_scan_v4 as scan


def test_focused_grid_is_preregistered_shape() -> None:
    pairs = scan.focused_grid()
    assert len(pairs) == 27
    assert len(set(pairs)) == 27
    assert scan.OLD_PAIR not in pairs
    assert (0.0, 0.875) in pairs
    assert (0.375, 0.875) in pairs
    assert (0.5, 0.875) not in pairs
    assert (0.625, 1.125) in pairs
    assert all(high - low >= scan.MIN_GAP - 1e-12 for low, high in pairs)


def test_all_pairs_adds_only_old_anchor() -> None:
    pairs = scan.all_pairs()
    assert len(pairs) == 28
    assert pairs[-1] == scan.OLD_PAIR
    assert len(set(pairs)) == 28


def test_labels_match_frozen_implementation() -> None:
    assert scan.label(0.375, 1.25) == "L0.375_H1.250"
    assert scan.label(*scan.OLD_PAIR) == "L1.000_H2.000"
