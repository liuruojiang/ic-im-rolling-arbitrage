# EV2-01 repair and local verification

Implemented shared `sina_mo_quote_variables` parser in `ic_im_signal_evidence_v1.py`. It consumes complete original assignments, rejects unsupported surrounding text, malformed identifier prefixes, empty payloads, duplicate or missing/extra identities and duplicate requested symbols. Capture, batch projection and both direct MO quote/opening-price consumers use it. Projection returns exact matched original bytes without reconstructing an identifier. Previously complete/hash-valid malformed archives fail when projected.

The opening-price consumer had the same root defect. Before changing it, its function text was verified identical to the committed backup. `open_before` preserves 9 failing rejection assertions (6 bad prefixes and 3 duplicate/extra cases), 3 passing controls, and one old spaces-format control failure. Scope addition was preregistered by the parent in addendum 01. Quote/open/expiry pricing semantics were not changed.

`run01`: 99 passed, 4 failed due to the parallel strategy repair's deliberate missing-mark rejection in old randomized property tests. Strategy agent updated those test expectations; no exclusions added here. `run02`: 112 passed, 0 failed, 0 skipped; includes all 42 new evidence repair tests and four related suites. It uses the isolated worker and temporary evidence only. Saved real 64-symbol batch and existing overlapping-batch replay tests pass.

Fixture: `quant_research_runs/20261022_free_source_integration_v1/wire_probe_01/response.bin`, SHA-256 `8ee4b6f3a1fd92392430213f7d81f6642deada308c84c50dfef58a91aeb2112c`. Price 74.0 and opening price 37.6 are the saved first packet; identifiers for second/third packets are synthetic mutations, not independently observed market quotes.

Parent still owns final whole-source hash binding, full daily/cumulative regression, real original-day recovery and independent adversarial review. This report is targeted validation only.
