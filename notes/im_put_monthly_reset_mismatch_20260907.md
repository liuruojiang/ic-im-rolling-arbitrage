# IM Put monthly reset mismatch — historical research needs revalidation

Confirmed 2026-09-07 following user challenge. This is an additive correction to research interpretation; frozen outputs, source engines and production were not changed.

## Contract and observed behavior

`docs/ic_im_system_mainlines_v1_spec.md` specifies monthly Put reset. `docs/ic_im_mainline_v1_3_r7_spec.md:8` preserves independent monthly Put maintenance when futures change to quarterly rolls. A monthly reset must reselect the approximately 95% strike even when the selected option expiry month remains the same. It does not imply moving the Put calendar to quarterly futures roll dates.

`im_mo_close_execution_v8.py::run_model_normal_close` rolls only when `desired != active.month`; when target month and quantity are unchanged it marks the old option. There is no independent monthly reset-date input in that function. This is not equivalent to the intended monthly strike reset.

Read-only reproduction using the actual frozen model calendar and schedules:

| Monthly reference date | Selected expiry month | Core option actually held | Core trade rows |
|---|---|---|---:|
| 2015-11-20 | 2016-03 | MODEL_1603_8478.1990 | 0 |
| 2015-12-18 | 2016-03 | MODEL_1603_8478.1990 | 0 |
| 2016-01-15 | 2016-03 | MODEL_1603_8478.1990 | 0 |

The core option was bought on 2015-11-03 and rolled on 2016-02-04. Therefore the previous explanation that IM naturally retains its November Put described the executed historical engine, not faithful implementation of the specified monthly strategy.

The same module's `run_real_normal_close` explicitly reuses the held contract when `active.contract_month == desired_month`; its normal path also lacks an independent monthly strike reset. Real-data studies that call this function need revalidation, not just the 2015 simulation.

The inspected research signal display code in `poe_ic_im_mainline_v1_3_bot.py` derives `option_roll_due` from an independent monthly reference and calls `select_im_put_for_reset` during monthly maintenance. This source-level observation does not constitute an end-to-end live acceptance test; it establishes that historical engine parity alone is insufficient for strategy parity.

## Evidence status and affected interpretation

- The IM full-model Put-only baseline, IV-exit scans, and 2016 loss attribution in this conversation describe the old engine path. Their numeric reproducibility does not validate the monthly strategy contract.
- Prior IM real IV scans relying on `run_real_normal_close` also require revalidation before using them to choose an IV threshold.
- IC's checked model runner accepts explicit independent monthly option roll dates and resets on those dates, even if expiry month stays unchanged. The comparison therefore changed more than product and target size.
- Existing 2015 IM valuation-signal coverage issue is separate and still unresolved.
- No corrected performance or preferred threshold is asserted here. Original results remain available as historical evidence but must not be presented as acceptance of intended monthly Put maintenance.

Correction work must use a new version, preserve independent monthly option calendars and unaffected futures paths, enforce same-expiry strike reselection with full close/open costs, and replay both core/momentum legs on model and real data. Tests must assert the intended calendar events rather than only reproducing old daily returns. The separate early-valuation coverage limit must remain explicit.

## Step 1 correction delivered 2026-09-07

Versioned research engine `im_put_monthly_reset_v1.py` implements explicit independent monthly resets, same-expiry strike reselection, both-side fees, and persistent real-quote execution requests. The matched baseline entrypoint is `research_im_put_monthly_reset_step1.py`; evidence is in `quant_param_scan_runs/20260907_im_put_monthly_reset_step1/record.md`. Ten focused execution tests, 35 numerical checks and 167 monthly reset coverage checks passed. The original v8 and frozen outputs were preserved. Future monthly-Put research must explicitly use the corrected engine; existing callers of v8 remain historical replays and are not implicitly migrated or revalidated by this step.

This acceptance covers execution and the current r7 no-grid/no-Call baseline only. Early valuation-signal reconstruction, historical parameter/IV scans, IC/IM matched comparisons and production end-to-end acceptance are not completed by it. The corrected simulation still retains the early valuation gap for attribution. Continue sequentially; no IV threshold is promoted by this repair.

## Step 2 early-signal reconstruction 2026-09-07

Following user instruction to continue, `research_im_put_early_valuation_step2.py` rebuilt the previously frozen v13 PB/official-ERP conservative tiers from raw historical files and applied them only to pre-2015-10-19 evaluation dates on the corrected monthly engine. Evidence: `quant_param_scan_runs/20260907_im_put_early_valuation_step2/record.md`. This is a historical lower-bound reconstruction, not vintage-2015 data or a complete early three-factor score. It changes 97 parent-target execution dates; later targets, futures and momentum are unchanged. Nineteen focused tests, 33 numeric checks and 135 monthly reset checks passed; strict artifact acceptance passed.

The current matched model MaxDD is 26.95% after reconstruction versus 37.24% with only the monthly fix and 34.35% without Put. Remaining maximum drawdown spans 2015-11-30 to 2018-10-12 and is not explained away by this early-signal correction. Real-history references are unchanged and were not rerun in step 2. Old IV scans and all broader strategy conclusions still require separate revalidation. No frozen source/output, production ledger or deployment changed.
