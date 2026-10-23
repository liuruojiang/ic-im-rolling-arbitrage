# Adjacent standalone collector timing defect

Confirmed P2: CaptureRunner.cycle samples its strategy clock before hashing/checking/loading the ledger but starts its advancing collection clock only later, inside the strategy lock. Legitimate frame updates during preflight can be judged to lie in the future.

Offline run01 executes actual cycle() without an explicit clock and actual readonly migrated-chain loading, injecting two seconds of preflight delay. Actual official_snapshot_evidence receives quote18:45:01 at wall18:45:02 but the observer remains18:45:00; cycle returns incomplete. The strict expected-captured assertion fails. The same operation inside an outer collection_clock returns captured;1failure/1positivecontrol.

This is an availability/evidence-retention failure, not ledger corruption. Ledger path/hash equality passes in both cases. Quote time and delay are synthetic; no claim that a live provider was observed hitting this window. Minimal proof substitutes capture_daily with a boundary invoking the actual official snapshot guard; it does not claim actual full-package sealing.

Proposed repair: establish collection context immediately after selecting cycle clock and before preflight or lock wait, while retaining all source pin/publication and readonly checks. Parent informed; this agent made no production edit.
