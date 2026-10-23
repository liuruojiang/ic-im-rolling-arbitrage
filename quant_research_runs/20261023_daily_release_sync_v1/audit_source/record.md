# Source / release independent audit — 2026-10-23

Scope: strategy 8038980de86251b2c5133da04e788dc2c49c84ff; automation 4d53d5fe3711403cd4a27eee5a0e2d276731bb2c. Read-only production audit; probe artifacts are isolated here.

## Result
No confirmed deployment blocker in this scope. All 115 declared local source/test/fixture bindings exactly match GitHub git blobs at the pinned strategy commit. All 29 declared runtime source SHA-256 values match the current workspace. The daily runner static local import closure contains 17 files, all present in the 29-file inventory. The daily workflow uses the same immutable strategy SHA for checkout, verification, and digest provenance.

The two repositories contain byte-identical manifest blobs: 5c1d3c8f525608abf783e63f9481a5921c3db42e, 17681 bytes. Strategy attributes include `* -text`; CRLF source hashes are therefore preserved in the Linux checkout. Isolated verifier probes accept valid inputs and reject changed runner bytes, LF-normalized source bytes, changed manifest bytes and a wrong checkout pin. See verifier_negative_probe.json. Git subprocess output was mocked only for this isolated verifier unit probe; it is not a real remote checkout test.

## Exact coverage limits
The 29 entries cover resident signal/ledger/digest modules, not every executable called by workflow steps. Migration is a separate pinned-repository program. A static traversal of all test imports reaches 118 files, but many are conditional research imports; it is not evidence they ran. Actual collection of the 43 declared test files succeeded (986 collected before the existing five deselections), with 75 local Python files loaded. Three loaded dependencies are outside the 115 before/after binding list: migrate_ic_im_v1_3_r6_to_r7_state.py, migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py, run_ic_im_v1_3_github_digest.py. The worker itself is included in the 115 binding list. See loaded_import_probe.json.

The fixed remote checkout pins these migration/legacy files too. No evidence of a wrong file or unverified migration being executed was found. Thus this is a reporting boundary, not a confirmed defect in the deployed release: describe the receipt as 115 declared bindings, not a complete fingerprint of every transitive test dependency. Do not expand historical exclusions or claim the static 118-file traversal was execution coverage.

This audit does not prove market-source availability, actual SMTP delivery, or today's inbox arrival.
