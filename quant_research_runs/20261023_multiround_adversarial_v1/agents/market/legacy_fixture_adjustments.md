# Declared legacy test-fixture updates

The coordinator explicitly authorized updates to these three old test files after full-suite execution identified fixture/contract failures. Each original was copied to the run's `backup/` before editing; an existing backup was not overwritten. `legacy_fixture_before.json` records the pre-edit hashes. No production file was changed in this step.

## 1. `test_icim_round3_source_validation_20261021.py`

The positive expiry projection originally claimed only three observed 510500 months `[202611, 202612, 202703]` and invoked a current-listing validator without binding the runtime clock. At the payload's actual 2026-10-21 observation date, the valid four-month observation is `[202610, 202611, 202612, 202703]` (the October fourth-Wednesday expiry has not occurred). The synthetic observation now includes the missing October month; total/end pagination counts include that row and the unrelated-underlying row.

`test_months_are_complete_sorted_and_other_underlyings_remain_out_of_scope` retains its unrelated-underlying and duplicate-month mutations, binds `runtime_clock(CLOCK)`, and expects exactly those four observed months. Pagination after appending the duplicate is six rows.

`test_existing_projection_without_pagination_fields_remains_compatible` retains its omitted-pagination compatibility assertion, binds the observation clock, and strengthens its old truthiness check to the exact four-month output or exact ETF quote dictionary according to its parameter. These are explicitly changed expected outputs/assertions, not hidden test relaxations. No case, parametrization, or rejection assertion was deleted.

## 2. `test_icim_round5_recording_semantics_20261021.py`

Both fake `requests.Response` constructors now set `_content_consumed=True` immediately after writing `_content`: the same-month-URL reuse example and the wrong-year rejection example. Their responses contain in-memory complete bodies and no underlying raw socket. Declaring that body consumed lets `requests.iter_content()` read the provided bytes under the new production streaming limit; it avoids trying to read `raw=None`. The repeated-request, identity, incomplete-session, and replay assertions remain unchanged.

## 3. `test_icim_expert_followup_replay_20261022.py`

`raw_chain_provider` makes the same `_content_consumed=True` declaration for its fully supplied in-memory SSE body. No request-address, identity, equality, replay, or ledger assertion was changed.

## Verification

`market_legacy_fixture_01`: **82 passed, zero failures/errors/skips**, covering all cases in the three files. All 28 production modules plus the three updated tests were hashed before and after execution; all 31 hashes were identical during the run. The existing 19-project acceptance set was not removed or excluded. Run artifacts are `market_legacy_fixture_01/legacy_market_fixture_binding_receipt.json`, `regression.xml`, and `regression.txt`; exact old/new diff is `agents/market/legacy_fixture_adjustments.patch`.
