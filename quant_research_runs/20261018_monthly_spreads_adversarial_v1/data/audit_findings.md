# Independent data-boundary adversarial audit

Scope: monthly report diagnostics, quote-frame input boundaries, numerical failure isolation. No production files changed; no network request, email, ledger write or deployment performed. Offline HTTP responses were injected for the actual official parsing path.

## Confirmed findings before repair

1. **P2 — Derived numeric overflow can block the entire stored close report.** An actual official quote CSV with finite positive prices `IM2610=1.0`, `IM2611=1e308`, `IM2612=6000`, `IM2703=5900`, valid 2026-10-15 source timestamp, and both vendor fallbacks unavailable is accepted by `fetch_cffex_quotes`. `monthly_spreads` marks the month-next pair `ok` while its annualized number is `-inf`. State `_jsonable` converts this to `null`, preserving `status=ok`. `render_stored_close_report` raises `TypeError: unsupported format string passed to NoneType.__format__`. One broken optional diagnostic therefore prevents rendering the other valid monthly group and the entire stored report. The input is deliberately extreme and not observed in saved real prices. The data path is reachable with a corrupted single available source. Evidence: `official_overflow_pipeline.json`, `probe_official_overflow_pipeline.py`, `overflow_stored_render.json`.

2. **P2/P3 — Conversion overflow escapes pair isolation.** A Python integer `10**400` in the next-month quote raises `OverflowError` in `float()` and prevents both groups from returning. The shared upstream normalizer already raises for this input and the source router isolates it, so the direct helper defect is confirmed but this exact case does not newly bypass the production quote router. Evidence: `boundary_results.json`, fixed regression `overflow-int-price`.

3. **P3 — Boolean `True` is accepted as price 1.0.** Both direct helper conversion and upstream `pd.to_numeric` accept `True`. A direct helper rejection can prevent malformed raw calls; after normalizer conversion the helper cannot recover the original boolean provenance. The upstream normalization issue predates this diagnostic change. Evidence: `boundary_results.json`, fixed regression `boolean-price`.

## Existing gates and isolation that held

- A wrong-date row with a current frame attribute is accepted by the standalone helper but rejected by the upstream frame validator. The normalized frame attribute is always rederived from the actual source rows.
- Mixed products and duplicate contracts are rejected upstream before monthly diagnostics.
- Raw listing metadata is rebuilt by the normalizer. An attacker mutating only attrs is not equivalent to an actual incomplete raw listing accepted by the source router.
- Missing/null/nonfinite/zero next-month quotes become N/A for month-next while month-quarter still calculates. Invalid quarter-only quotes symmetrically affect the quarter group.
- Missing intended far listings never fall back to another month.

## Saved official historical smoke check

IC: 2,756 day frames, 5,509 successful pair results, 3 unavailable. IM: 986 day frames, 1,971 successful pair results, 1 unavailable. For every successful result, raw stored-leg price subtraction matches with zero differences.

This is an arithmetic and plumbing cross-check, not an independent historical exchange-calendar oracle: the test deliberately calls the production expiry callback to select unexpired months. The four unavailable results are holiday-sensitive expiry sessions (IC 2016-09-19, 2018-02-22, 2024-02-19; IM 2024-02-19). Saved raw frames contain the allegedly expired front month with actual volume. These four cases must be interpreted as a calendar-coverage limitation pending independent calendar review, not proof of missing official quotes. Full rows and reason strings are in `real_unavailable_explained.json`.

The calendar agent subsequently independently confirmed the holiday issue and also showed successful-but-wrong pair selection on these dates. For example IM 2024-02-19 month-quarter is reported zero from March-March but should be 30.8 from February-March. This demonstrates why the successful arithmetic count must not be presented as a wholly independent calendar-correctness test. Calendar evidence is maintained in the sibling `calendar/` directory.

## Fixed independent regressions

`test_independent_numeric_isolation.py` contains six fixed expected-behavior cases, including Python boolean, NumPy boolean, integer conversion overflow, derived numeric overflow, JSON normalization and actual digest stored-renderer continuation. All six failed before repair. `pre_fix_pytest.txt` preserves the initial five-case output; `pre_fix_pytest_six.txt` preserves the expanded six-case output. Rerun after repair is required before closing this audit.

## Independent post-repair verification

The exact six fixed regressions now pass (`post_fix_pytest_six.txt`: 6 passed). The actual official CSV parser and source router were rerun with the unchanged extreme prices, valid whole-frame source timestamp and unavailable vendor fallbacks. The affected month-next metric now returns `unavailable` with reason `nonfinite derived spread`; month-quarter remains `ok` with its unchanged `-5999.0` subtraction. The actual stored-report renderer completes, includes both monthly labels, N/A for the failing group, both valid leg identifiers and `-5999.00` for the surviving group. Full report text and verification output are saved separately in `official_overflow_pipeline_post.json`; original failure evidence is preserved.

The existing upstream boolean limitation is explicitly still open: `pd.to_numeric` converts a raw True price to numeric 1.0 before the diagnostic receives it. A post-repair independent gate probe confirms the normalized price is 1.0 and the metric is still `ok`. See `post_bool_normalization_known_limit.json`. The repair closes direct Python/NumPy boolean handling inside the diagnostics, not whole-pipeline boolean provenance validation. No policy, risk or position-input changes were made by this auditor.
