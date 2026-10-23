from __future__ import annotations

from hypothesis import given, strategies as st

import poe_ic_im_v1_3_state as state


@given(st.dictionaries(st.text(min_size=1), st.integers(), min_size=1, max_size=12))
def test_ledger_digest_is_independent_of_dictionary_insertion_order(payload):
    forward = dict(payload)
    reverse = dict(reversed(list(payload.items())))
    assert state._digest(forward) == state._digest(reverse)
