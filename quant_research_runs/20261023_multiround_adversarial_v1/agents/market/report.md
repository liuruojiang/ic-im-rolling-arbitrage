# Market acquisition adversarial audit — original-code result

Audit owner: `/root/ma23_market`; date: 2026-10-23. Scope: ETF/expiry/option acquisition, source fallback, wire identity, response limits, evidence and transport error classification. No production code, old tests, frozen artifacts, or formal ledger were changed. All requests were intercepted offline.

## Bound result

Authoritative stage: `market_03`. **36 cases: 25 passed, 11 failed, 0 errors, 0 skipped.** Failures are strict counterexamples against the original production pin, not expected-pass assertions relaxed to match implementation.

The stage bound all 28 production modules and this agent's single test before and after execution. All 29 hashes were identical. The production pin was checked against `20261023_production_provenance_v1/delivery/capture_source_pin.json` before running. See `market_03/market_binding_receipt.json`, `regression.xml`, `regression.txt`, and `collection.json`. This result does not bind other agents' still-changing new tests.

Own test: root `test_icim_ma23_market_20261023.py`, SHA-256 `660c112c5da210bb87964647379e959806870780e7cd8e4d5ee3245ab32d74ad`. Runner: `agents/market/run_bound_market.py`. It deliberately requires the original production pin; use a separate explicitly bound after-fix runner for verification.

## Confirmed findings

### M1 — P1: primary SSE expiry projection can bypass stricter fallback and change adopted reset month

`fetch_sse_510500_expiries` checks projection pagination and individual month syntax, but not the observed month-set shape at the observation date. `fetch_510500_expiries_with_failover` immediately trusts that result. The actual `select_ic_put_for_reset` then ranks the returned dates.

Counterexample: on 2026-11-20, return a fresh-dated, explicitly complete SSE projection containing the plausible stale July listing `[202607, 202608, 202609, 202612]`. The real reset selector adopts 2026-12-23 rather than the expected 2027-03-24 obtainable from the supplied current Sina fallback `[202611, 202612, 202703, 202706]`. The fallback is not called. This also lets the candidate avoid the unknown-2027 expiry gate at this stage. The test intercepts the adopted expiry immediately before its calendar check; it does not manufacture a formal signal or prove a real position was contaminated.

This is **not** the already-disclosed omitted-pagination compatibility projection: all `total/begin/end` fields are explicit, consistent and complete. Current two/three-month subsets are also accepted despite failing the existing stricter fallback's listing-shape contract.

Failing tests (4):

- `test_sse_explicit_full_page_cannot_admit_stale_or_partial_listing` (three month sets)
- `test_stale_sse_listing_cannot_bypass_sina_and_change_adopted_reset_expiry`

Suggested fix boundary: validate observed SSE month sets with the same listing-shape contract as Sina and fall back on rejection. Do not generate unseen contracts. Retain the known safe rejection if a provider removes the expiry month early on expiry-day close.

Material: synthetic SSE projection with realistic months/date and explicit complete pagination; actual production selector. No real stale SSE response is asserted.

### M2 — P2: quote-variable extractor lacks an identifier left boundary

`_exact_quote_variables` searches a prefix anywhere in the body. The genuine Tencent identifier `v_sh510500` renamed to `not_v_sh510500`, or genuine Sina `hq_str_sh510500` renamed to `not_hq_str_sh510500`, is still accepted as the requested variable. Exact symbol checks after extraction cannot distinguish this namespace mutation.

Failing test (2): `test_wrong_identifier_namespace_cannot_be_accepted_as_exact_etf_variable[tencent|sina]`.

Suggested fix: enforce a declaration/identifier left boundary while retaining valid saved bare Tencent and `var` Sina declarations. An identifier-prefix negative lookbehind is one compatible minimum; stricter declaration parsing should be checked against the existing real packets.

Material: actual saved `20261022_free_ic_sources_v1/probe_02/raw/tencent_etf.bin` and `sina_etf.bin`, changing only the variable-name prefix. This establishes parser acceptance, not an observed upstream incident.

### M3 — P2: SSE JSON path does not apply the quote-response byte budget

`_sse_quote_json` requests a non-stream response and calls `response.json()` directly. Unlike other bounded quote readers, it does not enforce `MAX_QUOTE_RESPONSE_BYTES`, bound the consumed stream, or close in a `finally` path.

Counterexample: patch the budget to 32 bytes; return more than 40 bytes of valid JSON including leading whitespace. The response is accepted. This is a bounded-resource robustness gap; no incorrect price is demonstrated.

Failing test (1): `test_sse_json_source_respects_actual_quote_byte_budget`.

Suggested fix: reuse the existing bounded streaming read and guaranteed close, preserving recorded-response/mock compatibility.

Material: wholly synthetic valid JSON fault; no large allocation or external request.

### M4 — P1: normal MO Sina cross-check does not bind the returned variable to the requested contract

`verify_sina_option_quote` sends the correct requested `P_OP_mo...` symbol but extracts the first quoted RHS without checking the returned variable name or uniqueness. It then echoes the requested contract in its result. The live IM Eastmoney-MO path calls this verification for selected monitor/core/momentum rows and publishes the resulting verification note, so this is reachable during normal source fallback.

Using the first real saved `P_OP_mo2611P6000` packet, each of the following is accepted as `MO2611-P-6000`: variable renamed to `P_OP_mo2611P6100`; namespace changed from `hq_str_` to `not_hq_str_`; exact quote declaration duplicated. Only the selected variable text is changed and the authentic price/fields remain fixed.

Failing test (3): `test_mo_crosscheck_rejects_unrequested_or_ambiguous_returned_variable[wrong_contract|wrong_namespace|duplicate]`.

Passing control: `test_real_mo_crosscheck_packet_still_accepts_its_requested_identity`.

Suggested fix: use exact requested-symbol extraction with namespace/declaration boundary and exactly-one-result semantics before field and price validation.

Material: real `20261022_free_source_integration_v1/wire_probe_01/response.bin`; first packet has last 74.000 and source stamp 2026-10-22 15:00:00. Mutations are synthetic. No claim that Sina actually returned the wrong symbol is made.

### M5 — P1: MO ordinary last-trade cross-check accepts same-day future source time

In `verify_sina_option_quote`, the zero-volume midpoint branch applies `_validate_iv_monitor_times`, but the ordinary `source_last_quote` path only compares dates. Altering the real packet's 15:00 stamp to 18:01 while the query clock is 18:00 still succeeds.

The caller consumes `source_date` and does not get the original Sina timestamp, so its later checks cannot reject this particular future time. This finding does not challenge the intended historical cross-check allowance for a source date later than the expected historical date, and does not require applying a 20-minute live bid/ask freshness window to old last trades. The counterexample is specifically a source instant after the current collection clock.

Failing test (1): `test_mo_last_trade_crosscheck_cannot_accept_same_day_future_quote_time`.

Suggested fix: reject source timestamps later than the observation clock in every price-basis branch, retaining the existing historical semantics and genuine saved packet control.

Material: same real MO packet, changing only its timestamp. Synthetic future-time fault.

## Defenses that passed

The 25 passing cases cover exact full-symbol/duplicate rejection for Tencent and Sina ETF, valid final-source fallback after two malformed sources, missing/bool/invalid/pre-session SSE time rejection, transport versus validation aggregate error typing, preservation of `EvidenceUnavailable` in replay, literal-comma serialization limited to safe exact Sina endpoints/parameters, unchanged logical evidence keys despite that wire serialization, offline exact replay without missing inputs, and the unchanged real MO identity control.

These narrow controls do not substitute for other agents' end-to-end calendar, persistence, concurrency, or real original-package equivalence suites. No assertion is made about an actual undocumented production data incident, and no new strategy rule or historical backtest parameter was introduced.

## Retained stages

`market_01` and `market_02` contain an earlier 31-case revision (7 failures, 24 passes). The exact earlier test is retained at `agents/market/test_market_31_revision.py`. `market_mo_01` contains the first four MO cases (3 failures, one real positive control). **Use `market_03` for the current 36-case original-code conclusions**; its stale-list scenario was strengthened to a plausible historical July four-month shape before binding.
