"""Execute only the two affected entry/roll capture-and-replay cases."""
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
V5 = ROOT / "quant_research_runs/20261112_expert_round5_v5"
PIN = V5 / "candidate_source_pin_r2.json"
EXPECTED_PIN_SHA = "05ae4d5a238ae619b90cef7755085a1ec55043da32bba8571ad95601944dc0b6"
OUT = HERE / "closure_r2"
OUT.mkdir(exist_ok=False)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


assert sha(PIN) == EXPECTED_PIN_SHA
pin = json.loads(PIN.read_text(encoding="utf-8"))
extra = [Path(__file__), HERE / "test_provenance_closure.py",
         V5 / "audits/ledger/r1/test_ledger_listing_outcome_routes.py",
         V5 / "isolated_test_entry.py",
         ROOT / "quant_research_runs/20261112_expert_round4_v4/audits/ledger/test_listing_candidate_routes.py"]
names = set(pin["source"]) | {str(f.relative_to(ROOT)) for f in extra}
binding = lambda: {n: sha(ROOT / n) for n in sorted(names)}
before = binding()
assert all(before[n] == h for n, h in pin["source"].items())
(OUT / "source_before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
prior = V5 / "takeover_backup_r1/ic_im_evidence_adapter_v1.py"
r1 = json.loads((V5 / "candidate_source_pin_r1.json").read_text(encoding="utf-8"))
assert sha(prior) == r1["source"]["ic_im_evidence_adapter_v1.py"]


class MaskCollector(ast.NodeTransformer):
    count = 0
    def visit_FunctionDef(self, node):
        if node.name == "collect_listing_result":
            self.count += 1
            node.body = [ast.Pass()]
            return node
        return self.generic_visit(node)


masked = []
for path in [prior, ROOT / "ic_im_evidence_adapter_v1.py"]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    masker = MaskCollector()
    tree = masker.visit(tree)
    assert masker.count == 1
    masked.append(ast.dump(tree, include_attributes=False))
assert masked[0] == masked[1], "change extends beyond collect_listing_result"
assert r1["source"]["poe_ic_im_mainline_v1_4_bot.py"] == pin["source"]["poe_ic_im_mainline_v1_4_bot.py"]

temporary = Path(tempfile.mkdtemp(prefix="ledger_v5_closure_"))
env = {**os.environ, "TEMP": str(temporary), "TMP": str(temporary),
       "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
       "ICIM_TEST_OUTPUT_DIR": str(OUT), "ICIM_DISABLE_INTERNAL_REFRESH": "1",
       "MPLCONFIGDIR": str(temporary / "mpl"), "ICIM_EVIDENCE_DIR": ""}
entry = V5 / "isolated_test_entry.py"
test = HERE / "test_provenance_closure.py"
start = datetime.now(timezone.utc)
with (OUT / "process_output.txt").open("w", encoding="utf-8") as stream:
    child = subprocess.Popen([sys.executable, "-B", "-X", "utf8", str(entry), str(test)],
        cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
    try:
        status = child.wait(timeout=60)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True)
        child.wait()
        status = 124
after = binding()
(OUT / "source_after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
result = {"exit_code": status, "started_at": start.isoformat(),
    "finished_at": datetime.now(timezone.utc).isoformat(), "watchdog_seconds": 60,
    "pin_sha256": sha(PIN), "main_sha256": pin["main_sha256"],
    "adapter_sha256": pin["source"]["ic_im_evidence_adapter_v1.py"],
    "source_files": len(before), "source_before_equals_after": before == after,
    "source_matched_r2": all(after[n] == h for n, h in pin["source"].items()),
    "adapter_AST_outside_collect_listing_result_unchanged": True,
    "unchanged_adapter_AST_sha256": hashlib.sha256(masked[0].encode()).hexdigest(),
    "public_query_and_policy_bot_byte_unchanged": True,
    "formal_ledger_write": False, "synthetic_market_controls": True,
    "test_file_sha256": sha(test), "entry_sha256": sha(entry),
    "worker_sha256": sha(ROOT / "_icim_delivery_test_worker.py")}
if (OUT / "regression.xml").is_file():
    cases = ET.parse(OUT / "regression.xml").findall(".//testcase")
    result["tests"] = [{"name": c.attrib["name"],
        "status": "failed" if c.find("failure") is not None else
                  "error" if c.find("error") is not None else
                  "skipped" if c.find("skipped") is not None else "passed"} for c in cases]
    result["xml_sha256"] = sha(OUT / "regression.xml")
    collection = json.loads((OUT / "collection.json").read_text(encoding="utf-8"))
    result["executed_nodes"] = collection["executed"]
if (OUT / "network_guard.json").is_file():
    result["network_guard"] = json.loads((OUT / "network_guard.json").read_text(encoding="utf-8"))
with (OUT / "receipt.json").open("x", encoding="utf-8") as stream:
    json.dump(result, stream, ensure_ascii=False, indent=2)
print(json.dumps(result, ensure_ascii=False))
assert status == 0 and before == after and result["source_matched_r2"]
assert len(result["tests"]) == 2 and all(c["status"] == "passed" for c in result["tests"])
assert len(result["executed_nodes"]) == 2
assert not result["network_guard"]["external_connection_attempts"]
