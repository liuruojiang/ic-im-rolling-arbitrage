# Round4 test contract adaptation

Only `test_icim_round4_evidence_boundaries_20261021.py` changed in this follow-up. No production or original 28 evidence cases changed.

`test_batch_projection_rejects_invalid_recorded_http_status` still rejects each None, False, string "200", and 600 status. The production metadata guard now rejects before projection: finish marks the session incomplete and load_session rejects it. The test now checks `complete=False`, confirms a `raw_http_metadata` failure, and includes load_session inside the expected-failure context. The old downstream http_response check remains inside that context. Error matching accepts the earlier incomplete-package rejection or original invalid-status rejection. No cases were deselected.

The complete file ran through the isolated runner: **24 passed**, one third-party Pydantic deprecation warning, exit 0. Artifacts: `evidence_round4_gate_contract_01` under the audit run directory.

Backup: `test_icim_round4_evidence_boundaries_20261021.before_gate_contract.py` in this directory. Backup creation explicitly refused to overwrite an existing file.
