# Independent strategy/state adversarial audit

Outcome: no new confirmed defect in this scoped audit of the final patched sources. **35 passed, 0 failed, 0 skipped, 0 deselected**, with all 9 bound source/test files byte-identical before and after. XML, selected/executed test identity and six artifact hashes validated through `icim_delivery_test_evidence.validate_receipt`; final receipt in `final01/receipt.json`.

The evidence repair author independently reviewed another agent’s strategy/state patch. The review checked policy consumption conditions, finite positive marks, day/contract binding, entry-cost validation, authoritative ledger anchor use and live/original-evidence `required_day` behavior.

Executable adversaries use a real temporary StateStore and the public IC builder: pending/cost-basis/expiry/route/contract/early-roll/opening-plan/date and ENTER/ROLL action mutations, missing/zero/negative/NaN/infinite/boolean/non-numeric marks, and missing/wrong bindings. Every rejection reopens the ledger and verifies its digest stayed unchanged. A separate positive control really appends and reopens the valid current-day bound mark, avoiding rejection evidence based only on an invalid surrounding fixture.

Further controls cover IC/IM missing confirmed entry costs, lagging/duplicated/NaN/unsorted primary close series falling back to the valid dated backup, and pre-10/16 IC/IM new-entry signals. Those historical entry controls remain eligible without inventing an already-held mark requirement. The final ENTER/ROLL exemption cannot bypass the independent held-leg guard during append.

`run01` retains an earlier 31-pass check; `entry_probe01` retains two pre-10/16 entry controls. `final01` reruns all 35 against the stabilized source. No assertion was weakened and no case was excluded.

Limits: full public-builder/StateStore negative proof is IC, while IM coverage here is policy and historical-entry boundaries. Holding/market mutations are synthetic; preserved SSE identity fixture is used through the existing public-builder helper. No network, formal ledger, remote publication or account execution. Parent owns cumulative regression and real original-day recovery.
