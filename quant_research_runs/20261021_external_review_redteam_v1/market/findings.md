# Independent market audit — 2026-10-21

Scope: saved Sina market packets, IC identity and same-leg IV, IC adjusted-contract isolation, CFFEX monthly archive recovery. Read workspace rules and mainline records before review. No deployment, mail, live account access or production-ledger writes. Only the new independent test and this evidence directory were written by this role.

## Reproducible findings before additional repairs

| ID | Severity | Trigger and location | Observed impact |
| --- | --- | --- | --- |
| MARKET-1 | P1 | `fetch_sina_510500_chain`: details are not checked against requested IDs, underlying field 36, complete expiry date, unique security IDs or unique generated trading codes. `_quote_row` takes the first match. | A real saved 510500 packet with underlying changed to 510300 still becomes a standard 510500 Put. A conflicting duplicate wins according to input order. Real reset and short-candidate consumers compute IV and quantities using these prices. A payload expiry changed from 2026-11-25 to 2026-11-01 is silently priced with the reconstructed 2026-11-25 expiry. |
| MARKET-2 | P2 | `fetch_sina_510500_chain` accepts a response omitting one requested standard security. | Both `select_ic_put_for_reset` and `_v14_ic_short_put_candidate` move a 6.76 strike target from `510500P2611M06750` to `510500P2611M07000` when security 10012482 disappears. Short candidate remains `tradable=True`; this is an actual choice change rather than just internal malformed data. |
| MARKET-3 | P2 | `_cffex_historical_quote_frame`: cache eviction covers ZIP shape/RuntimeError during member reading, but not CSV parse failures or post-read schema/numeric validation. | Missing required CSV columns, empty CSV, invalid GBK bytes and all-NaN closes fail closed but leave the bad historical-month body cached. A second call with a now-valid HTTP response available still fails on the cached bytes with **zero HTTP calls**. Recovery is blocked for the lifetime of this process unless cache is manually cleared. No stale quote is accepted in this scenario. |

The immutable `first_failure.txt/xml` records the initial 6 failures / 5 passes. `pre_fix_full_failure.txt/xml` records all 21 counterexample failures / 18 passes, including the eight real-consumer scenarios and a cache-recovery scenario. Later output must be interpreted separately from these preserved failures.

## Evidence quality and controls

- Packet prices and identities originate from saved real responses. Changes to underlying, expiry, ID sets or prices are explicitly synthetic adversarial mutations, not asserted historical market observations.
- Real-consumer tests keep Black–Scholes inversion, Delta and quantity calculations intact. Mocked external dependencies are the saved chain, fixed 2% rate, independent original security-ID mapping, and deterministic collection clock. The reset test supplies the one captured listed month to isolate chain identity rather than fabricate a full available term structure.
- Initial correct literal quote control confirms last=.1669 from field 2, not ask field 3. NaN/infinite/negative price and strike checks pass.
- Missing-day archive refresh is bounded, and missing-day refresh timeout leaves no stale cache. Duplicate members, encrypted members and broken ZIP structure are rejected and evicted.
- Fifteen additional IV/A controls pass: bound core evidence overrides another leg's price/time; malformed/nonfinite metadata and stale/future/wrong-day timestamps produce unavailable IV without calling the pricing function; core overlay rejects a security-ID mismatch; a stale row remains visible but cannot be consumed; the real December chain retains all 15 legal M contracts while quarantining all 14 A rows.
- Existing A-contract support is explicitly incomplete and already disclosed in the registry. The independent checks confirm fail-closed isolation for all seven held/target/profit fields and continued admission of ordinary M anchors and non-IC data. They do not certify full A support or all historical ledger versions.

The review compared the supplied `source_delta.patch`; the new same-leg IV evidence and ZIP duplicate/encryption/missing-day guards close their tested boundaries. The findings above concern remaining identity/coverage and later cache-validation paths rather than treating the earlier 989-pass result as proof of completeness.

## Reproduction

From repository root:

```powershell
python quant_research_runs/20261021_external_review_redteam_v1/market/run_checks.py test_icim_redteam_market_20261021.py
```

This is an independently copied runner with explicit `ROOT=parents[3]`, its own `HERE`, fresh disposable `ICIM_STATE_DIR`, and evidence-local output. It does not overwrite the prior repair run's output. The test-only Windows directory-mode workaround is preserved in the runner and recorded in the run JSON.

## Source fingerprints at pre-repair review

| Artifact | SHA-256 |
| --- | --- |
| `poe_ic_im_mainline_v1_4_bot.py` | `010b56719875d8f55590d184e134c61c30afe16a67ea020ca874808ae8f48c73` |
| `ic_im_put_execution_v1.py` | `2e30546c65fe1991d1b96edc44afb7e2d7de103b8f236353ddd100997829e74d` |
| supplied `source_delta.patch` | `1e05631057f204f550bb6edf68cd3605756e8111a3c0a349e0f29c73f50a2633` |
| real November quotes | `065514dac504e7c70cae346c5aecfb1c9de792d98b27f9787c02fb30d354808f` |
| real December quotes | `14c527e8b43d924481421fe399eef9adbaddb2960989beda99365c9563d55111` |

## First repair verification and second-round bypass

The original 54 tests passed unchanged against bot SHA-256 `1be3211a2541b996dccfc69dbe67321e78bdda22cb048bf538fa1e07fbb9f708`. Separate `post_fix.txt/xml` and `post_fix_run.json` preserve that result. Earlier failures remain untouched.

A further consumer-level check then found **MARKET-4 (P1)**: `fetch_sina_510500_security_id` repeats the old, weaker response parsing independently of the repaired chain parser. Both reset and short-candidate consumers call it after selecting a row from the validated chain, and replace the selected row's security ID with the second result. The new test first supplies an authentic full chain with ID 10012482, then supplies the actual second lookup with an unchanged requested-ID list but a detail response changing that ID to 99999999. Both consumers return `contract=510500P2611M06750, security_id=99999999`. There is no mocked ID-lookup result in this test. The failure is therefore observable price/security misbinding in the real candidate, and may affect later dated-open price lookup.

`second_lookup_first_failure.txt/xml` preserves these two failures. The test suite now contains 56 cases. This later finding means that the first 54-pass result alone does not establish complete closure of market identity binding.

## Final second repair verification

The final independent suite contains **61 tests, all passed**, with unchanged earlier business invariants. The extra five cases exercise the direct mapper used when an SSE row lacks a security ID: one authentic mapping control and four rejection cases (unrequested ID, wrong underlying, wrong complete expiry and fractional strike). Their initial result, four failures / one pass, is retained in `lookup_direct_initial.txt/xml`.

The two consumers now reuse the validated Sina row's security ID. The mapper used for SSE rows calls the same strict Sina chain parser, and rejects A identity because its master/unit remain unavailable. This closes MARKET-4 within the tested actual consumers as well as the mapper itself. MARKET-1/2/3 also retain their original passing counterexamples after the second repair.

Second-repair bot SHA-256 before newline normalization: `5ce06870228dd83ecac6078cd4032b7290f7e8879329eddf7af36afbaff7f74e`. The test suite SHA-256 is `bf6dd3432c34948e6cc091e7e22bb8a382be585953e0b6ba63d4f35290b871df`. `round2/source_before.json` and `round2/source_after.json` are equal; `round2/post_fix.txt/xml` and `round2/post_fix_run.json` preserve that run.

After the producer restored LF line endings, all **61 tests passed again** on the final byte-level bot SHA-256 **`6e56f723bfdd32649911c6d7980b41e46611b9620b5776233109d177b51ffdd3`**. `round2/final_lf_source_before.json` equals `round2/final_lf_source_after.json`, confirming source stability during this final verification. Final output is separately retained in `round2/final_lf.txt/xml` and `round2/final_lf_run.json`; no initial failure or earlier run was overwritten.

Residual scope: this is an offline adversarial audit of saved true packets plus explicit mutations, not a guarantee against every upstream behavior or proof of current network reachability. Full adjusted-A contract support remains unavailable by design and is safely isolated in the checked paths. SSE provides an aggregate chain time rather than the Sina per-contract timestamps; these tests do not invent stronger source evidence. The archive findings and repairs were verified through `_cffex_historical_quote_frame`; this role did not certify all other archive consumers such as `fetch_cffex_daily_marks`. No production ledger was used or modified.
