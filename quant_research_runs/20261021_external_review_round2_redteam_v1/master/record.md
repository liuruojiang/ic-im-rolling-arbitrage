# Independent adjustment master audit

2026-10-21. Scope: `ic_im_sse_contract_identity_v1.py`, official saved adjustment/master/quote responses and diagnostic resolver. No production source, existing tests, old evidence, frozen specifications, outputs or operational state were changed by this auditor.

Initial run: `ICIM_AUDIT_STAGE=master/initial python quant_research_runs/20261021_external_review_round2_redteam_v1/run_checks.py test_icim_round2_audit_master_20261021.py`.

28 tests: 25 passed, 3 failed. Initial assertions and actual calls are preserved in `initial/regression.xml`, `initial/regression.txt`, and `initial/contradiction_*.json`. Source, test, runner and raw-source SHA-256 values are bound before and after the run with no differences. `initial/test_run.json` identifies the disposable TEMP/state paths and test-only Windows mkdir compatibility adjustment.

## Confirmed findings sent to coordinator for repair

1. P2 diagnostic integrity: `parse_adjustment_events` accepts explicit nonempty `actionErrors` and `fieldErrors` alongside real results. Two single-field synthetic contradictions in the actual official envelope each still returned 114 parsed events. An error-labelled response must not be treated as successful identity evidence. These are synthetic source-error injections, not observed official-source failures.
2. P2 diagnostic chronology: the same parser ignores `START_DATE`. For real security `10011470`, changing only `START_DATE` from `20260423` to `20260716` leaves `ADJUST_DATE=20260715`; the parser still returns an event supposedly effective before the original security starts trading. This is an internal date contradiction, not an attempt to demand independent authentication of consistently fabricated authority data.

Both findings concern diagnostic input validation. They do not establish a production execution vulnerability: the resolver explicitly retains `engine_supported=false`, and no A-contract cost or ledger migration is implemented or authorized.

## Independent real-data reconciliation

The actual saved byte hashes match the earlier capture and fixture bytes. Independently checked all 114 unique adjustment-event identities, original and adjusted codes, strikes, units and dates. All 58 quote securities match the 58-row official current master by security ID, code and strike; all 14 live adjusted Puts resolve to the exact official unit and expiry. Raw quote is dated 2026-10-21 at 14:11:23; the master itself is dated 2026-10-20. No assertion implies the master was updated on the quote date or verifies broker holdings.

Existing nonfinite, Boolean numeric, nonintegral-unit, original-strike, expiry/month, duplicate event, wrong alias and stale/effective interval guards passed. The resolver left its event and quote inputs unchanged and retained its explicit unsupported status for both M and A aliases.

## Limits

Official-source authenticity/capture provenance remains a caller responsibility. Arbitrarily fabricated but internally consistent official records are outside the reported bugs. This audit neither implements nor claims complete A-cost maintenance, historical migrations, real-account validation, quote-time freshness beyond the API's date-level contract, trading permission or publication readiness. No network requests were required.

## Coordinator repair and independent recheck

After the coordinator repaired explicit error-envelope and listing-interval validation, a separately preserved run under `master/schema_initial` passed the original 28 cases and failed two new schema assertions. Numeric `isPagination=0` and `0.0` compared equal to Python `False` and still produced 114 events. This third finding is P3 diagnostic schema ambiguity; its first actual results and failing assertions are in `schema_initial/pagination_schema_{int,float}.json` and `schema_initial/regression.xml`. The original assertions were not weakened or removed.

The coordinator then required the literal Boolean `False` or string `'false'`. Final independent run under `master/post_fix` passed all 30 tests in 0.67 seconds. All five contradiction calls now reject with `RuntimeError`. The real 114-event, 58-quote and 14-adjusted-Put reconciliation still passes. Source/test/runner/raw SHA-256 values before and after this final run are identical; exact digests are in `post_fix/source_before.json` and `post_fix/source_after.json`. Earlier initial and schema-initial evidence was preserved.

No test imports the bot, constructs a `StateStore`, or reads/writes operational runtime files. The three runner invocations explicitly selected only this test file and independently allocated TEMP/state paths recorded in their `test_run.json` files. The reviewer did not execute the older reconciliation script (it was read as source only), issue network requests, alter or restore runtime state, or claim that unrelated background advancement of the operational ledger was caused by these tests.

## Final-source binding after the market auditor's ASCII validation changes

The coordinator requested one final independent recheck after the identity module's ASCII regular-expression hardening. Under `ICIM_AUDIT_STAGE=master/final`, the same 30 assertions all passed in 0.57 seconds. Only the audit fixture's hash-input list was extended; no test assertion or production module was modified by this auditor. `final/source_before.json` and `final/source_after.json` are equal and additionally bind the current root bot, identity, policy, state and Put-execution modules, this test file and runner. All earlier evidence, including `post_fix`, remains unchanged. The selected test module still imports only identity, pytest and the standard library; the runner's final disposable TEMP/state path is recorded in `final/test_run.json`.
