# Independent takeover: strategy, durable state and display anchors

No new confirmed product defect was found in this scoped review of the current patch over `58986fda2c17df93eb3925bf09a1cebd55a7869e`. Production sources and historical evidence were not edited.

The final run is `final03/receipt.json`: **131 passed, 0 failures, 0 errors, 0 skipped**. XML and stdout are retained with SHA-256 hashes. All 533 bound files were identical before/after: 509 root Python/source/test bindings and 24 files under the actual formal `runtime/ic_im_v1_4_r1` directory. This does not claim a new comparison of every historical runtime directory or frozen output.

Static inspection followed the old-held-leg mark requirement from `_apply_v14_live_policy` through pure policy consumption and `derive_next_anchors`. The latter checks the independently projected durable holding before applying new closing plans, so mutating the output route, pending status or ENTER/ROLL action cannot remove the old mark requirement. The entry/roll exception in the signal-only check does not bypass the append check. The display ContextVar is scoped separately from the same-day predecessor calculation anchor; existing tests exercise latest-head display, same-day recomputation, error restoration and own-head capture.

Four executed groups:

- Existing root strategy repair regressions.
- Existing root server repair regressions (including head, clock and restoration boundaries).
- Existing independent 35-case strategy/StateStore audit.
- Nine new IM durable-state controls in `test_im_durable_mark.py`.

The new IM controls use **synthetic holdings and signals with the actual isolated StateStore**. A valid current-day bound mark really appends and reopens with sequence increment. Eight mutations (missing mark, wrong day, wrong contract, forged pending, ENTER, ROLL, cost basis, or expiry exemption) reject and preserve all ledger payload bytes. Network calls are forbidden. These are not real IM supplier/public-builder end-to-end evidence; that previously disclosed coverage limit remains.

Preserved harness history:

- `probe01.xml`: positive control failed because the newly written fixture inherited a candidate contract month inconsistent with its expiry. The fixture contract was corrected explicitly to `MO2612-P-7200`; no product code changed. `probe02.xml` then passed all nine cases.
- Top-level `final.xml` and `receipt.json`: collection failed because the ad hoc harness omitted the existing required `ICIM_TEST_OUTPUT_DIR` environment variable. No tests executed in that attempt.
- `final02/`: 131 cases passed, but its requested runtime path was nonexistent and contributed zero hashes. That run proves only the 509 source/test bindings. The harness was corrected to `runtime/ic_im_v1_4_r1`, now asserts a nonempty snapshot, and `final03/` is the final evidence.

No test assertion was weakened, no cases were excluded, and failed attempts were not overwritten. No network, message sending, remote deployment or production ledger append occurred in this audit.
