# State/server adversarial audit — authorized repairs

The original 21 cases remain frozen in `test_icim_ma23_state_20261023.py`.
The selection extension is a separate four-case file,
`test_icim_ma23_state_selection_20261023.py`. Synthetic packet values in routing
tests are deliberately not market facts, valid executions, or real source captures.

## Confirmed findings and repairs

| Finding | Reachable trigger and effect | Repair |
|---|---|---|
| S1, P1 | A collector fails before sealing a manifest. An incomplete cycle receipt, or the server's `last_evidence_capture` failure, exists, but the page previously omitted the missing-original-evidence warning. | Read failed cycle receipts separately from sealed attempt counts; surface same-day server pre-seal failures after close. A normal `waiting_for_close` receipt is still silent. No ledger or recoverability qualification is inferred from a receipt. |
| S2, P1 display/recovery readiness | A structurally valid original packet has an obsolete input revision, missing required replay roles, or an acquisition finish after the original session day. The loader rejects it but archive status previously reported captured. | Readiness and actual loading share exact revision, required-role, and original-session clock qualification. This is a false readiness report; the audit did not demonstrate incorrect ledger acceptance. |
| S2 extension, P1 recovery | An older valid packet coexists with a newer packet ending after midnight. Archive status finds the good packet, but the prior loader selected the newer packet before validating its clock and then refused recovery. | Apply original eligibility per candidate before selecting the latest eligible packet. A rejected newer packet cannot hide a valid earlier packet. |
| Concurrency-agent finding | Four collector workers finish, but pool shutdown consumes the remainder of the single-product budget before sealing. | Check `guard(8)` after all pool writers finish, still inside `_network_budget(45)`. The check does not increase the configured budget or promise interruption of an in-flight request. |

Production edits in this task are limited to `ic_im_evidence_adapter_v1.py` and
`poe_ic_im_v1_4_server.py`. No strategy, valuation, position-sizing, or historical
backtest formula was changed. Evidence loading now accepts the latest eligible
original packet rather than allowing an ineligible newer packet to block it.

## Bound validation

| Receipt stage | Result | Bound files |
|---|---|---|
| `state_03` | Original audit: 21 cases, 5 failures, 16 passes | 28 production modules + original state test |
| `state_after_01` | First repairs: original 21 passed | Same 29 files |
| `state_selection_before_01` | New selection extension: 4 cases, all 4 failed | 28 production modules + new selection test |
| `state_joint_after_01` | Original 21 + selection 4 + frozen concurrency 9 = **34 passed** | 28 production modules + all 3 test files |

Each listed stage records source hashes before and after; all hashes were stable
through its invocation. The original failure receipts were preserved. This audit
used disposable temporary stores and explicitly prohibited external networking;
it did not mutate official runtime state, real original packages, or frozen
research/backtest artifacts. Parent performs the consolidated runtime hash check
and full regression run, so this local result is not a claim of global release
completion.

Coverage includes both-product rejection without partial persistence, bad markers
on journal crash recovery, repeated safe recovery without a second append,
early bound-copy rejection across threads, renderer exception cleanup, and a real
snapshot reader waiting for a temporary anchor projection to be restored.

The old statistics fixture originally named
`test_icim_evidence_conflict_review_20261022.py::test_sealed_attempt_statistics_count_conflicts_not_polling_receipts`
used only its `minimum(session)` probe and did not establish the replay role
inventory or original acquisition clock. The parent owns any isolated fixture
adaptation and preserves its original bytes; this agent did not weaken the new
qualification or edit that test.
