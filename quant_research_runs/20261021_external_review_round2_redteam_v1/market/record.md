# Independent market audit — 2026-10-21

Scope: SSE six-field quote projection, failover, held security identity, existing
IC Put sizing, core overlay, and short/reset consumers. Official raw fixture:
`tests/fixtures/icim_external_review_round2_20261021/sse_december_identity.json`.
All rows and their prices/IDs remain unchanged except the explicitly identified
single-field mutations. No production source, old fixture, old evidence, formal
ledger, or other auditor's assertions were edited by this auditor.

## First failures, preserved

- `initial/`: 31 tests, 13 failed / 18 passed. Matching source/test snapshots and
  before/after hashes are in this directory. Production bot SHA-256 was
  `ED0A1417F1D663F0F95DA67BDA080A2B637BE4F0D3B58AD4B644C5D64ED61128`.
- `initial_short/`: 2 tests, 1 failed / 1 passed, using the same bot SHA-256.
- `full_initial/`: the combined 33 tests, 14 failed / 19 passed, matching bot
  before/after hashes and test snapshot preserved.
- `additional_initial/`: 5 further tests, 2 failed / 3 passed. They show Unicode
  IDs reaching both reset and short candidates, and a valid backup surviving
  primary ID/strike/price corruption. The independent module now has 38 cases.
- `unicode_initial/`: 4/4 further cases failed against the main agent's first
  patch (`AA63AE51E7D2C288A8B8FC1704BCED9472E162E32385E0CED3C2AC63F0501526`).
  They cover direct selected-ID / held-ID / held-contract guards and one
  Unicode-ID mutation in the previously captured real Sina three-packet fixture.
- `fallback_initial/`: 2/2 lifecycle fallback cases failed after the main
  agent's first short-identity guard. The independent module now has 44 cases.
  That first sampling overlapped the main agent's unrelated regex changes, so
  its differing before/after bot hashes preclude treating it as a single-version
  acceptance. The identical two failures were repeated in `first_patch/` under
  matching bot hashes (C0A4C99378F5BCC7BA432F08026DDE54CF0300BAF5218AF8F5EE5FD8C76A05E5):
  42 passed / 2 failed. All original market findings except MKT-05 passed there.
- `preproof_initial/`: an additional expiry case fails when candidate collection
  throws before held-short identity validation is reached. This ensures a repair
  cannot rely only on detecting exceptions emitted by the late identity guard.
  The independent module is now frozen at 45 cases for final verification.
- Two setup statements were then changed to configure the request mock without
  eagerly fetching before the assertion. This permits a correct repair in the
  parser itself; failure expectations were not relaxed. The first test snapshot
  remains under `initial/`.

## Confirmed findings

1. **MKT-01 — P1, invalid/future aggregate timestamps reach pricing consumers.**
   Only change `payload.time` to `235959`, `246001`, `invalid`, or `null` while
   clock is 2026-10-21 14:15 Beijing. Both held-core overlay and short candidate
   consume the otherwise real 58-row response. The short candidate is tradable;
   the core writes the invalid/future time into its IV evidence. SSE parsing only
   stringifies/zfills time; failover does not validate it; consumer date checks
   ignore time; per-row time checks return early for SSE rows with no timestamp.
   This finding does not require SSE to supply per-row 20-minute metadata.

2. **MKT-02 — P2, unusable primary suppresses a usable backup.** Only change
   `payload.date` to previous day / next day / invalid text. A fresh backup is
   available. Failover returns the primary, held-core validation subsequently
   rejects it, and the backup has never been called. This is unnecessary loss of
   research availability, not acceptance of an unsafe current signal.

3. **MKT-03 — P3, strict identity syntax accepts non-ASCII exchange codes.**
   Replacing the selected real M row's ID or strike suffix with full-width digits
   passes `\d` and integer conversion; both reset and short candidates publish
   the non-ASCII security ID. This is a schema/canonical-identity gap;
   no claim is made that it alone allows pricing a different real security.

4. **MKT-04 — P1, short holding bypasses the repaired held-identity gate.**
   Use the synthetic pre-adjustment held short anchor M07500 / 10011470, no core
   Put, and synthetic confirmed original entry 1.5. Actual fixture security
   10011470 is now A07500 with last 0.67, strike 7.367. `fetch_option_closes`
   is represented by that exact actual security's price. New-candidate
   availability is independently disabled. `_apply_v14_live_policy` consumes the
   A price against the old M cost and returns `WAIT_SHORT_PUT_ROLL`, setting the
   waiting-roll lifecycle state. Correct M07500 / 10012099 is the passing
   control. Existing short mark collection fetches by stable ID but never checks
   its current code/strike against the held anchor. This is a reachable old-M
   label bypass of unsupported A rejection, not a request to implement A cost
   migration.

5. **MKT-05 — P1, rejected held identity can still mutate lifecycle fallback.**
   The main agent specifically requested checking the expiry and preloaded-mark
   cases while repairing MKT-04. On synthetic expiry date 2026-12-23 (explicitly
   not a future real capture), the same real stale M/A identities trigger a held
   identity error, but the broad handler invokes policy again and preserves
   `PUBLISH_SHORT_PUT_EXPIRY_BRANCHES` and settlement-pending state. On the actual
   fixture date, a preloaded `v14_short_put_mark=.01` survives the identity error:
   displayed action becomes `HOLD_FAIL_CLOSED`, yet `v14_roll_pending=True` is
   generated inside the fallback. Both cases need failed identity to prevent
   position-dependent policy transitions, not merely rename the final action.
   A third case raises a candidate transport error before the held identity
   check; fallback still publishes expiry branches. Existing identity therefore
   must be proven before dependent lifecycle evaluation, including exceptional
   paths.

The synthetic anchors are test inputs, not claims about a broker account or the
formal ledger. Unsupported A migration, perfectly consistent forged authorities,
and declared historical compatibility boundaries are excluded as new findings.

## Isolation

The runner sets unique TEMP/TMP and `ICIM_STATE_DIR` before importing production
modules. The Windows 0700 mkdir/chmod workaround applies only in this disposable
test process. No production task, email, deployment, or default StateStore was
used. The live `runtime/latest.json` was not written by this auditor.

## Final independent verification

`final/regression.xml` and `final/regression.txt`: **45 passed**, zero failures,
one third-party Pydantic deprecation warning, 2.01 seconds. Source, independent
test, runner, and real fixture before/after SHA-256 records match exactly. Bot:
`0B729074DD8C0908A5049ACF08CDF17BC69A1485E8F87D15C2F59EB9A596FA14`.
Independent test:
`6EDE68BB2DFDC96135E538E80CE902CF3B32A69BB575D1117C166B78C70FE46E`.

All five findings are closed in this independently rerun scope: invalid/future
timestamps are rejected, bad primary dates permit valid backup selection,
ASCII identity syntax is enforced at source and direct guards, existing short
identity is proven before lifecycle/candidate work, and unverified identity
cannot pass through expiry/roll fallback. Valid same-security core/short/reset
and primary-failure backup controls remain passing. Final snapshots are under
`final/`; first-failure evidence remains unchanged.

This verifies local research code and injected failure boundaries. It does not
authorize A migration, prove broker positions, verify future actual December
quotes, or approve live deployment or orders.
