# Cloud compatibility release, 2026-11-12

Release: `20261112-expert-review-v5-r3`. This supersedes the r2 deployment inventory without changing its frozen manifest, specification, hashes or original audit outputs.

Real Python 3.11 cloud run 43241651015 executed the complete 56-file suite: 1569 passed, two failed and the original five deselections remained. Its retained child output and XML identified two environment contracts: the transport exception's initialization did not accept requests keyword arguments on Python 3.11, and Pandas 3 changed the typed SSE-frame replay comparison. The new release explicitly delegates the exception initializer to requests and pins the already verified Pandas 2.3.3. It does not change the typed codec, equality assertions or source-validation rules.

Independent whole-module AST comparison confirms that only the new exception initializer changes executable producer behavior. All 30 other execution modules, the evidence codec/adapter and the calendar remain byte-identical to r2. The two original failed cases pass locally with identical source bindings; these local object/replay checks do not claim cloud Python 3.11 execution.

A subsequent local full run executed all 1571 cases with zero assertion failures but exceeded the unchanged 60-second outer watchdog. It is rejected as a formal acceptance result. Its raw outputs remain available; no replacement passing receipt was created. Final cloud promotion still requires the full original gate on the actual Python 3.11 runtime, including zero failures/errors/skips and the original five deselections.

The release inventory contains 31 execution modules, three support files and the same 56 declared test files. The current cloud consumer must explicitly require r3. The previous successful 2026-10-22 replay is reusable only within its unchanged successful-path AST and codec; it is historical evidence, not a current quote or trading instruction. This release procedure does not send mail, write the formal ledger or change the daily schedule.
