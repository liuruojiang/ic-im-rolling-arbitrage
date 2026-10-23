# Post-fix execution regression verification

2026-10-21: re-ran the same 76 independent execution tests against the current
source after the parent agent's parser/cache/display fixes.

Result: **76 passed**, no failures; one existing fastapi_poe Pydantic
deprecation warning. The refusal-to-write, exact original-anchor restoration,
coexisting ordinary/short/profit branches, monthly priority, crash recovery,
and duplicate-append boundaries retain their previous results.

Evidence: `post_fix.xml`, `post_fix.txt`, `post_fix.json`, isolated runner
`run_execution_post_fix.py`, and actual current source SHA-256 values in
`post_fix_source_hashes.json`. Execution/policy/state bytes are unchanged
from the first audit; bot bytes changed as expected. The historical
`source_delta.patch` and initial `source_delta_comparison.json` remain
historical evidence, and matching that old patch is not a post-fix requirement.

The first 68-case run and expanded 76-case run were preserved. No production
code, original tests, or previous outputs were modified. This is a synthetic,
isolated execution regression check; it does not add any live exchange or
account evidence. The source hashes were captured immediately after the run,
so no exact source-immutability claim across the entire run is made.
