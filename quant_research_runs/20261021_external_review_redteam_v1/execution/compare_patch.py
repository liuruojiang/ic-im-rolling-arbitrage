"""Read-only, newline-normalized comparison against recorded review patch."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
TARGETS = {"ic_im_put_execution_v1.py", "ic_im_v1_4_policy.py",
           "poe_ic_im_v1_4_state.py", "poe_ic_im_mainline_v1_4_bot.py"}
patch = ROOT / "quant_research_runs/20261021_external_review_v1/source_delta.patch"
lines = patch.read_text(encoding="utf-8-sig").splitlines()
results = []
target = None
i = 0
while i < len(lines):
    line = lines[i]
    if line.startswith("+++ after/"):
        target = line[len("+++ after/"):]
    match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
    if match and target in TARGETS:
        after = []
        start = int(match[1]) - 1
        i += 1
        while i < len(lines) and not lines[i].startswith(("@@ ", "--- before/")):
            if lines[i].startswith(("+", " ")):
                after.append(lines[i][1:])
            i += 1
        actual = (ROOT / target).read_text(encoding="utf-8-sig").splitlines()
        exact = actual[start:start+len(after)] == after
        locations = [n + 1 for n in range(len(actual)-len(after)+1) if actual[n:n+len(after)] == after]
        results.append(dict(file=target, recorded_after_start=start+1,
                            lines=len(after), exact_at_recorded_line=exact,
                            matching_locations=locations))
        continue
    i += 1
report = dict(method="UTF-8 decode and splitlines normalize only line endings; compare every recorded after-hunk",
    source_delta_sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),
    files={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in sorted(TARGETS)},
    hunks=results, all_match=all(r["exact_at_recorded_line"] for r in results))
(HERE/"source_delta_comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(dict(all_match=report["all_match"], hunks=len(results),
                     unmatched=[r for r in results if not r["exact_at_recorded_line"]]), indent=2))
