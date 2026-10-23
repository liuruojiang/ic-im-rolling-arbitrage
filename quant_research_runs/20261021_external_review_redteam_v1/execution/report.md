# Independent execution and persistence red-team audit

Audit date: 2026-10-21. Scope: `ic_im_put_execution_v1.py`,
`ic_im_v1_4_policy.py`, `poe_ic_im_v1_4_state.py`, and ordinary/committed
openings in `poe_ic_im_mainline_v1_4_bot.py`.

## Result

No confirmed reachable defect in the audited repair's execution-evidence or
ledger-transition boundaries. This is a scoped negative result, not approval
for deployment, live/account orders, or the project's uncompleted scope.

The independent first run passed 68 cases. After adding short-plus-ordinary
coexistence, public-wrapper restoration, and actual calendar monthly cases,
the expanded run passed 76 cases. Both runs are retained separately. The only
warning is the existing `fastapi_poe` Pydantic deprecation warning.

## Independent evidence

- `first_run.xml`, `first_run.txt`, `first_run.json`: immutable first execution.
- `expanded_run.xml`, `expanded_run.txt`, `expanded_run.json`: final 76 cases.
- `run_execution.py`, `run_execution_expanded.py`: isolated runners; ROOT is
  the actual workspace, HERE is this evidence directory; temporary state is
  outside all production runtime directories. Existing runner was read and
  copied, never executed or modified.
- `source_delta_comparison.json`: hashes of the four audited production files
  and the recorded source patch; all 25 relevant after-hunks match current
  source exactly after normalizing line endings. `compare_patch.py` makes the
  check repeatable. A preliminary `git apply --reverse --check` did not apply
  the patch; this did not modify files. The explicit decoded-line comparison
  resolved that check's ambiguity.
- New independent test source:
  `test_icim_redteam_execution_20261021.py` at workspace root.

## Boundaries challenged

1. Ordinary, short-Put, and profit3x plans due on 2026-10-21: omitted events,
   `closed_missing_open_price`, `closed_missed_execution_day`, and forged
   `closed_monthly_priority` cannot erase the commitment on a nonmonthly day.
2. 2026-10-20/21 boundary: preboundary old-close compatibility remains; an
   obligation cannot be hidden once computing a new boundary-day signal.
3. Monthly priority: actual 2026-11-20 calendar date skips same-day ordinary
   and profit openings without fetching prices, but still fetches and requires
   committed short-Put opening evidence. Overdue profit plans require replay
   even with monthly priority.
4. Ordinary independent momentum plus profit3x opening: either unresolved
   branch blocks the gate; both successful branches retain momentum and set
   the new fixed-core premium.
5. Ordinary independent momentum plus committed short opening: successful
   projections retain both legs. Failure before/after the ordinary projection
   restores the public query's exact original anchor object and content.
6. Short required-price evidence: missing/duplicate rows, wrong execution
   date/security ID, empty source, infinity, and negative price are rejected
   without mutating the input anchor.
7. Ordinary persistent evidence: missing/duplicate rows, wrong day/identity,
   empty source, and infinity fail before any JSON ledger bytes change.
8. Recovery: simulate journal commit followed by failure before latest.json
   replacement; restart recovers one sequence, clears the executed plan,
   retains exactly two journal records including bootstrap, and rejects
   duplicate append without changing the recovered digest.

## Candidate rejected as a reachable finding

`require_due_openings` exempts ordinary plans whenever `monthly_priority` is
true, including an injected overdue ordinary plan. Bot's ordinary adapter
would instead return retry for an overdue day before reaching monthly
priority. However, `_validate_record` binds ordinary signal_day to the
anchor's last_verified_day and execution_day to its immediate next exchange
day; `derive_next_anchors` also advances exactly one exchange session.
Therefore the tested production ledger path cannot naturally carry that
overdue ordinary state into a later monthly session. This is an API-level
defense-in-depth inconsistency, not a demonstrated production write bypass.

## Attribution and limitations

The new omission guards and retry statuses were compared with the recorded
source delta. No repair-induced reachable regression was reproduced. The
audit did not treat the previous 989 PASS total as proof and did not rerun
that cumulative suite.

All injected prices, source labels, event conditions, and crash points are
synthetic. Complete legacy schema fixtures are reused for ledger tests, with
only the new gate's effective date moved to the fixture's day to isolate the
execution invariant from unrelated Fear provenance. Actual 2026-10-21 gates
and 2026-11-20 monthly logic are separately exercised without moving dates.
No full live-data end-to-end build is claimed; no networking, mail, broker,
deployment, or official ledger writes occurred. Direct helper tests do not
prove all upstream producers supply authentic exchange evidence. A-contract
support, official contract-master provenance, actual historical fillability,
external scheduler concurrency, real power-loss durability, 2027 calendars,
full strategy returns, and the explicitly uncompleted release scope remain
outside this execution audit. The tests verify failed evidence and one
specific journal/latest crash boundary, not every possible filesystem fault.

Only the new test file and files under this execution evidence directory were
created by this audit. Production code, previous tests, frozen specifications,
and previous output evidence were not changed.
