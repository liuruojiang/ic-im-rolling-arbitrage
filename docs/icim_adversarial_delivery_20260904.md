# ICIM adversarial delivery audit — 2026-09-04

Scope: published r7 source at `2ff781f348aa8ebe1925671881b47c2c8c317000`; offline fault injection, isolated temporary ledgers, no production catch-up, mail, orders, or strategy changes.

## Reproduced defects and narrow changes

1. `close_confirmed="false"` (also other truthy non-booleans) could pass `bool()` and commit a close record. Boundary validation now requires an actual Boolean before append, persisted-record validation, and artifact publication.
2. Injected NaN/Infinity in displayed total exposure could be serialized to null and committed. Present total-current/target exposure now must be a real finite nonnegative number; numeric strings and booleans are not accepted. Optional diagnostic fields retain existing N/A semantics.
3. Wrong realtime phase and expected market date were rejected only after coordinator construction (and realtime phase after catch-up). These request preconditions, plus the positive session limit, now run before coordinator initialization.

The first independent 24-test run had 12 failures and 12 passes. After the patch and expansion, all 39 new tests passed. This is fault-injection evidence, not evidence that malformed production signals occurred today.

## Evidence

- Exact GitHub regression selection plus the new suite: `python -X utf8 -m pytest -q test_delivery_transport_retry.py test_ohlcv_provider_validation.py test_poe_ic_im_v1_3_state.py test_run_ic_im_v1_3_github_digest.py test_adversarial_icim_delivery.py`: **80 passed, no skips**, one existing Pydantic deprecation warning.
- Wider source/state/quarter-roll/migration/property selection: 169 passed, 3 skips for absent local historical migration/scan artifacts. This is supplementary, not the required delivery acceptance suite.
- Unmodified accepted GitHub run 33850626309 journal pair is preserved in `tests/fixtures/icim_adversarial/`. New code derives exactly the same September 4 product anchors and accepts the unchanged close report.
- Fault injection covers independent IM core/momentum Put fields, IC no-Call, anchor/next-date mismatches, digest mutation, weekend/time-zone normalization, nonfinite realtime totals, truthy fake confirmation, crash between journal and latest replacement, failure output replacing stale success, and bounded retry counts (two transport attempts; no validation-error retry).
- A recovered orphan journal retains the originally committed signal rather than replacing it with the second source response.

## Test-process isolation

Importing the server creates a coordinator. Reproduced: inheriting production `ICIM_REQUIRE_MIGRATION=1` while pointing an ordinary pytest process at an empty temporary state causes collection to fail before any test. Regression subprocesses must set `ICIM_REQUIRE_MIGRATION=0` and a unique temporary `ICIM_STATE_DIR`; production runner subprocesses must retain `ICIM_REQUIRE_MIGRATION=1`. The new test module isolates and restores its import environment and also passes all 39 tests under a parent migration-required environment.

Backup before implementation edits: `.codex_backups/20260904_164210/`.

## Remaining boundaries

- Sparse legacy/bootstrap test signals may omit total exposure fields; this patch validates those fields when present, not a wholesale signal-schema migration.
- This audit does not promise third-party data or Gmail availability. Retry count is bounded, but each transport attempt retains the existing network budget; OS-level hangs and a send-success/marker-write crash still need workflow-level handling.
- Import-time ASGI coordinator construction remains; callers must supply isolated test state before import.
- Existing stale-writer/dual-writer guards were rerun. Multiple different coordinators concurrently sharing the module-global strategy runtime were not newly certified; production daily runners use separate processes and workflow serialization.
- No strategy parameters, execution timing, Put rules, IC no-Call rule, or production ledgers were changed.
