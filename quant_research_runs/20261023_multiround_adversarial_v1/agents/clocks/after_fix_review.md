# Clocks / cache independent repair review

Date: 2026-10-23. Agent: ma23_clocks. Offline only; no formal ledger or frozen performance output written.

## Result

CLOCKS-1 (P1) is closed for the reviewed source binding. The frozen 19 adversarial cases now pass independently: **19 passed, 0 failed**. Before repair, the same test file produced **17 passed, 2 failed** in `before_02`. The post-fix run is `after_fix_01`; its before/after hashes match for 28 production modules and this agent's unchanged test (29 files total).

The two failed-before cases cover a known Sina-source chain with a missing per-row `source_stamp`, through original-session capture and typed evidence replay. This is acceptance of an incorrect or legacy typed producer input, not a claim that the normal current Sina wire parser actually drops the field.

## Production fix independently inspected

The fix adds an optional explicit source argument to the consumed-row timestamp gate. A selected row from a source identified as Sina is rejected when its row timestamp is missing. An SSE row may continue to use the aggregate SSE timestamp contract.

Source propagation is present at all five reviewed IC consumption paths: reset selection and short Put candidate pass `stamp.source` directly; the independent core overlay and held short identity pass `stamp.source` through `_quote_row`; the main held IC Put path passes its `sse_stamp.source`. Other reviewed `_quote_row` uses are MO paths. The fix requires no DataFrame attribute or evidence adapter rewrite.

The gate applies to consumed rows. It does not reject a whole chain merely because an unrelated row is stale. The frozen SSE compatibility and stale-unused-row controls pass. The selected nearest stale strike still cannot be silently replaced by a different fresh strike.

## Verification evidence

- `test_confirmed_sina_candidate_cannot_omit_consumed_row_timestamp_in_capture`: passed after fix, failed before.
- `test_confirmed_sina_candidate_cannot_omit_consumed_row_timestamp_in_typed_replay`: passed after fix, failed before.
- `test_sse_candidate_without_row_timestamp_keeps_aggregate_source_contract`: passed.
- `test_known_stale_unconsumed_row_does_not_invalidate_entire_chain`: passed.
- `test_stale_nearest_row_is_not_replaced_by_other_fresh_strike`: passed.
- Remaining frozen cases cover cache-source-date revalidation and mutation isolation, active-time 20-minute boundary including lunch, elapsed-clock application once, worker context, immutable replay clock, nested fear-batch/collection-scope restoration, and malformed/future aggregate stamps: all passed.

The test uses synthetic prices/clocks and existing production CaptureSession, EvidenceReader, query-cache, candidate and validation functions. It makes no network request. Pricing stubs isolate timestamp/evidence admission from financial-model correctness.

Artifacts: `after_fix_01/receipt.json`, `after_fix_01/source_before.json`, `after_fix_01/source_after.json`, common run `../../clocks_after_fix_01/regression.xml`, and `verification_after_fix.json`. Original failure report and `before_02` evidence are retained. The earlier `initial_01` typed-replay fixture used a wrong month key; only corrected `before_02` is relied on for the two-failure comparison.

## Limits

This is scoped closure of CLOCKS-1 and its 19 frozen checks, not approval of every multi-round repair or a fresh historical performance recomputation. No production file or old test was modified by this agent. Other agents' independent findings and the root's final full-suite source binding remain necessary for overall acceptance.
