# Publication guard repair

Date: 2026-10-23. This changes only `ic_im_signal_evidence_v1.py` in production. The collector integration is owned by the history agent.

## Finding and boundary

P1: a collector's post-capture source-pin verification used to run after a complete original-input manifest was already published. A reader could accept that manifest before a later error reported the unpinned executed dependency. A post-publication check cannot close this race.

The new `publication_guard(callback)` is a scoped ContextVar context manager. A CaptureSession retains its creation scope's zero-argument callback. `_finish_locked` calls it before computing completeness or publishing any manifest. Callback exceptions append an unresolved `publication_guard` failure, preserve the original exception object in `session.publication_error`, and produce an incomplete archive. Bound signals retain `persistence_eligible=False`. The guard does not raise over an existing calculation exception. The collector must run its final source validation to report source errors, including paths where capture fails before sealing.

The guard defaults to None. Unguarded callers are unchanged. Nested scopes restore the outer callback; copied thread contexts inherit it. A new retry session can succeed after a failed scope without reusing or repairing the earlier incomplete archive. The callback only rejects publication; it cannot grant eligibility beyond existing signal and evidence checks.

## Validation

Original `test_icim_ma23_evidence_20261023.py` is unchanged: 28 cases pass.

New offline synthetic `test_icim_ma23_publication_guard_20261023.py`: seven cases pass:

- `test_publication_guard_rejects_before_any_complete_manifest_is_visible`: callback runs before a manifest exists, publication contains only complete=False, a reader immediately after publication rejects, bound signal is incomplete/nonpersistent, original guard exception retained.
- `test_publication_guard_fresh_retry_scope_is_not_poisoned`: failed attempt isolated; a new guarded retry succeeds; subsequent unguarded capture remains normal.
- `test_publication_guard_nested_scopes_restore_outer_callback`.
- `test_publication_guard_copy_context_worker_inherits_callback`.
- `test_publication_guard_retained_when_session_outlives_callback_scope`.
- `test_publication_guard_does_not_mask_original_computation_exception`.
- `test_publication_guard_invalid_callback_does_not_replace_active_scope`.

Combined independent run: **35 passed**, exit 0. Before/after binding includes 28 production modules and both test files, **30 unchanged hashes**. Receipt: `publication_bound_01/receipt.json`; test artifacts: `../../evidence_publication_bound_01/` relative to this report's directory. This validation uses temporary synthetic evidence only, blocks external requests, and does not write formal runtime or frozen archives.

## Scope

Added `_publication_guard` and `publication_guard`. Updated `CaptureSession.__init__` and `_finish_locked`. No change to matching, calendar, HTTP metadata rules, codecs, raw-response conflict behavior, old tests, or existing original evidence.

Root/history must validate the collector integration and cumulative production binding before final release.
