# Market identity, clocks and collector takeover audit

Read-only production review of the v3-r3 working tree above baseline 58986fda. No new confirmed code defect was found in this scope. Current-day collection is nevertheless blocked by socket permission errors; code-test success does not establish live-source availability.

## Checks performed

- Reviewed exact Tencent variable and embedded code checks, EastMoney code/time checks, consuming live_proxy timestamp validation, strict Sina whole-body assignment parsing and its capture/direct-open/direct-close/archive-projection call sites.
- Reviewed collection_clock monotonic advancement, nested-context behavior, server lock acquisition and worker clock sampling, capture anchor install/restoration and collector preflight placement. Observation time advances while strategy day stays fixed. Cancellation does not forcibly kill a worker already executing; this remains the documented boundary.
- Re-ran six existing author and independent suites offline against current files: market repairs, evidence repairs, server repairs, independent server, collector preflight, independent market/evidence. Result: **192 passed, 0 failed, 0 errors, 0 skipped** in 40.84 seconds. One third-party Pydantic deprecation warning. These are repeated existing probes, not 192 newly authored tests. XML is `pytest.xml`; disposable files and emitted observations are confined to this audit directory.
- Recomputed all 29 capture-pin file hashes and the audited-manifest hash: all match. Details: `pin_verification.json`.

## Current collector observation

At 2026-10-23 15:08:53 +08:00, PID 36668 exists as Python and has the expected 14:57:41 start time. The latest receipt started 15:07:57 and finished at approximately 15:08:06; IC and IM are both **incomplete**. Both product failures include WinError 10013 (socket access denied), across market providers. The receipt reports the verified ledger at 2026-10-22 / sequence 20 and unchanged ledger paths/bytes. Its capture pin matches the audited pin. `collector_observation.json` records the exact receipt path and hash.

The 14:57 `waiting_for_close` receipt is historical startup evidence, not the current operational status. No complete 2026-10-23 original-day package is established by these observations. Restoring allowed network access is an operational follow-up; reducing validation cannot repair it. No collector start/stop, network request, message, ledger write or production-source modification was performed by this reviewer.

Process command-line inspection via CIM was denied; process association uses Get-Process PID/start-time and matching receipts, not a claim of independently inspected current command-line arguments. This audit does not establish remote Poe/GitHub deployment or current-day real-data recovery.
