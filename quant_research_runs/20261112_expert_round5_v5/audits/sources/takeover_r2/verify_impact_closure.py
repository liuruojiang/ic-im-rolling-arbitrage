"""Bind the preserved source audit to r2 without rerunning its 37 passed tests."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
PRIOR = HERE.parent / "r1"
V5 = HERE.parents[2]
ROOT = V5.parents[1]
R2_PIN_SHA = "05ae4d5a238ae619b90cef7755085a1ec55043da32bba8571ad95601944dc0b6"


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path):
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def ast_sha(node):
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


assert sha(V5 / "candidate_source_pin_r2.json") == R2_PIN_SHA
r1 = load(V5 / "candidate_source_pin_r1.json")
r2 = load(V5 / "candidate_source_pin_r2.json")
assert r2["parent_candidate_sha256"] == sha(V5 / "candidate_source_pin_r1.json")
assert set(r1["source"]) == set(r2["source"]) and len(r2["source"]) == 213
changed = [name for name in r2["source"] if r1["source"][name] != r2["source"][name]]
assert set(changed) == {"ic_im_evidence_adapter_v1.py", "test_icim_expert_round5_20261112.py"}
before = {name: sha(ROOT / name) for name in r2["source"]}
assert before == r2["source"]
backup = V5 / "takeover_backup_r1/ic_im_evidence_adapter_v1.py"
assert sha(backup) == r1["source"]["ic_im_evidence_adapter_v1.py"]
old_tree, new_tree = tree(backup), tree(ROOT / "ic_im_evidence_adapter_v1.py")


class RemoveChangedHelper(ast.NodeTransformer):
    def __init__(self):
        self.removed = []

    def visit_FunctionDef(self, node):
        if node.name == "collect_listing_result":
            self.removed.append(ast_sha(node))
            node.body = [ast.Pass()]
            return node
        return self.generic_visit(node)


old_filter, new_filter = RemoveChangedHelper(), RemoveChangedHelper()
old_outside = old_filter.visit(old_tree)
new_outside = new_filter.visit(new_tree)
assert len(old_filter.removed) == len(new_filter.removed) == 1
assert old_filter.removed != new_filter.removed
assert ast.dump(old_outside, include_attributes=False) == ast.dump(new_outside, include_attributes=False)
unchanged_adapter_ast_sha = ast_sha(new_outside)

# The only changed helper is confined to new-day ic.months capture. The
# 37 source-role cases call quote-chain/expiry paths and no listing capture.
source_test = PRIOR / "test_reference_clock_sources.py"
source_ast = tree(source_test)
calls = sorted({ast.unparse(n.func) for n in ast.walk(source_ast) if isinstance(n, ast.Call)})
assert not any("capture_session" in call or "fetch_510500_expiries" in call for call in calls)
assert not any(isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("test_")
               for n in ast.walk(source_ast))

old_summary = load(PRIOR / "summary.json")
assert sha(source_test) == old_summary["test_source_sha256"]
assert old_summary["main_sha256"] == r2["main_sha256"]
for name, digest in old_summary["artifact_hashes"].items():
    assert sha(PRIOR / "run" / name) == digest
collection = load(PRIOR / "run/collection.json")
assert collection["collected"] == collection["selected"] == collection["executed"]
assert len(collection["executed"]) == 37 and not collection["deselected"]
cases = ET.parse(PRIOR / "run/regression.xml").findall(".//testcase")
assert len(cases) == 37 and not any(list(case) for case in cases)
assert sorted(case.attrib["name"] for case in cases) == sorted(node.split("::", 1)[1]
                                                                for node in collection["executed"])

after = {name: sha(ROOT / name) for name in r2["source"]}
assert after == before
result = {
    "status": "passed_r2_source_impact_closure_and_r1_test_reuse",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "candidate": "candidate_source_pin_r2.json",
    "candidate_pin_sha256": R2_PIN_SHA,
    "main_sha256": r2["main_sha256"],
    "r2_source_count": len(before),
    "r2_entire_source_exact_before_after": True,
    "r1_entire_213_source_still_exact": False,
    "r1_changed_files": changed,
    "adapter_r1_backup_sha256": sha(backup),
    "adapter_changed_function": "_keyed.adapted.collect_listing_result",
    "adapter_ast_identical_outside_changed_function": True,
    "adapter_ast_with_changed_helper_removed_sha256": unchanged_adapter_ast_sha,
    "changed_helper_ast_sha256": {"r1": old_filter.removed[0], "r2": new_filter.removed[0]},
    "main_program_and_expiry_module_byte_unchanged": True,
    "source_test_no_listing_capture_or_root_test_imports": True,
    "reused_independent_tests": 37,
    "new_tests_executed": 0,
    "original_counts": old_summary["counts"],
    "original_artifact_sha256": {str((PRIOR / "run" / name).relative_to(ROOT)): digest
                                for name, digest in old_summary["artifact_hashes"].items()},
    "prior_source_test_sha256": sha(source_test),
    "no_open_findings_in_source_scope": True,
    "reused_tests_do_not_validate_new_listing_capture_provenance_helper": True,
    "production_or_ledger_or_network_operations": False,
    "scope_limits": [
        "The earlier r1 whole-source equality is historical and is not a claim about r2.",
        "Listing-capture provenance changed and requires its own root/surface/ledger validation.",
        "Source-role tests use controlled clocks and saved genuine reports; no current source availability claim."
    ],
}
for name, value in [("binding_before.json", before), ("binding_after.json", after),
                    ("summary.json", result)]:
    with (HERE / name).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
print(json.dumps({k: result[k] for k in ["status", "r2_source_count", "r1_changed_files",
    "reused_independent_tests", "new_tests_executed"]}, ensure_ascii=False))
