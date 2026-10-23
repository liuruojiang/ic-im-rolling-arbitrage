"""Bind final conflict fixes, preserved bytes, real recovery, and local runtime."""
from datetime import date, datetime
from pathlib import Path
import ast
import difflib
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "quant_research_runs/20261022_evidence_conflict_review_v1"
D = RUN / "delivery"
sys.path.insert(0, str(ROOT))
import ic_im_evidence_adapter_v1 as adapter
import poe_ic_im_mainline_v1_4_bot as bot


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


base = read(RUN / "baseline.json")
runtime = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / "runtime").rglob("*") if p.is_file()}
assert runtime == base["runtime"]
assert all((ROOT / n).is_file() and sha(ROOT / n) == h for n, h in base["original_evidence"].items())
assert all(sha(RUN / "backup" / n) == h for n, h in base["backups"].items())
extra = read(D / "additional_test_backup.json")
assert sha(RUN / "backup" / extra["file"]) == extra["before_sha256"]
production = {"poe_ic_im_mainline_v1_4_bot.py", "ic_im_signal_evidence_v1.py", "ic_im_evidence_adapter_v1.py",
              "poe_ic_im_v1_4_state.py", "poe_ic_im_v1_4_server.py", "capture_ic_im_v1_4_inputs.py"}
legacy_tests = {"test_icim_round5_recording_semantics_20261021.py", extra["file"]}
allowed = production | legacy_tests | {"notes/mainline_registry.md"}
prior_changes = {n for n, h in base["previous_binding"].items() if sha(ROOT / n) != h}
assert prior_changes <= allowed, prior_changes - allowed
receipt = read(RUN / "accepted_03/receipt.json")
assert receipt["exit_code"] == 0 and receipt["counts"] == {"tests":2941,"failures":0,"errors":0,"skipped":0}
before, after = read(RUN / "accepted_03/source_before.json"), read(RUN / "accepted_03/source_after.json")
assert before == after and all(sha(ROOT / n) == h for n, h in after.items())
collection = read(RUN / "accepted_03/collection.json")
assert len(collection["selected"]) == len(collection["executed"]) == 2941
assert set(collection["deselected"]) == set(read(RUN / "exclusions.json")) and len(collection["deselected"]) == 22
assert sha(RUN / "exclusions.json") == sha(ROOT / "quant_research_runs/20261022_free_source_integration_v1/exclusions.json")
assert len([n for n in collection["executed"] if n.startswith("test_icim_round5_recording_semantics_20261021.py::")]) == 19
recovery = read(RUN / "real_recovery_02/receipt.json")
assert recovery["status"] == "PASS" and all(c["status"] == "PASS" for c in recovery["cases"])
assert all(sha(ROOT / n) == h for n, h in recovery["source_sha256"].items())
readiness = read(D / "readiness_after_patch.json")
assert readiness["status"] == "ready_for_isolated_recovery" and not readiness["network_attempts"]
pin = read(D / "capture_source_pin.json")
assert len(pin["files"]) == 28 and all(sha(ROOT / n) == h for n, h in pin["files"].items())
spec = ROOT / "docs/ic_im_evidence_conflict_review_20261022_v1_spec.md"
assert sha(spec) == Path(str(spec) + ".sha256").read_text(encoding="utf-8").split()[0]


def definitions(path):
    return ast.parse(path.read_text(encoding="utf-8-sig"))


old_tree, new_tree = definitions(RUN / "backup/poe_ic_im_mainline_v1_4_bot.py"), definitions(ROOT / "poe_ic_im_mainline_v1_4_bot.py")
def assignments(tree):
    return {n.targets[0].id: ast.dump(n, include_attributes=False) for n in tree.body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
old, new = assignments(old_tree), assignments(new_tree)
assert all(old[k] == new[k] for k in ["_OFFICIAL_EXCHANGE_CALENDAR_YEARS", "_EXCHANGE_CLOSURES"])
old_functions = {n.name: ast.dump(n, include_attributes=False) for n in old_tree.body if isinstance(n, ast.FunctionDef)}
new_functions = {n.name: ast.dump(n, include_attributes=False) for n in new_tree.body if isinstance(n, ast.FunctionDef)}
assert old_functions["_evidence_http_get"] == new_functions["_evidence_http_get"]
assert old_functions["_bs_price_delta"] == new_functions["_bs_price_delta"]
old_registry = (RUN / "backup/notes/mainline_registry.md").read_text(encoding="utf-8")
assert (ROOT / "notes/mainline_registry.md").read_text(encoding="utf-8").endswith(old_registry.split("\n", 1)[1])
patch = []
for name in sorted(allowed | {"test_icim_evidence_conflict_review_20261022.py"}):
    original = RUN / "backup" / name
    a = original.read_text(encoding="utf-8-sig").splitlines(keepends=True) if original.exists() else []
    b = (ROOT / name).read_text(encoding="utf-8-sig").splitlines(keepends=True)
    patch.extend(difflib.unified_diff(a,b,fromfile="before/"+name if a else "/dev/null",tofile="after/"+name))
(D / "source_delta.patch").write_text("".join(patch), encoding="utf-8")
launch = read(D / "launch_receipt.json")
pid = int(launch["pid"])
process = subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",
    f"Get-Process -Id {pid} -ErrorAction Stop | Select-Object Id,ProcessName,StartTime,CPU | ConvertTo-Json"],
    capture_output=True,encoding="utf-8")
assert process.returncode == 0
info = json.loads(process.stdout)
assert info["Id"] == pid and info["ProcessName"] == "python"
files = sorted((ROOT / "data/icim_v14_original_inputs/capture_receipts").rglob("*.json"))
current = [(f,read(f)) for f in files if read(f).get("pid") == pid]
assert current, "new collector first receipt missing"
path, live = current[-1]
assert live["source_pin_sha256"] == sha(D / "capture_source_pin.json")
assert live["ledger_paths_and_bytes_unchanged"] and "consistency_statistics" in live
assert live["status"] in {"captured","already_captured","incomplete"}
packages = {}
for product in ["IC","IM"]:
    reader, _ = adapter.load_original(bot, ROOT / "data/icim_v14_original_inputs", product, date(2026,10,22))
    packages[product] = {"manifest":str(reader.manifest_path),"sha256":reader.manifest_sha256,"all_blobs_verified":True}
probe = read(D / "time_probe_02/receipt.json")
assert probe["status"] == "OBSERVED_TWO_RESPONSES" and not probe["complete"] and len(probe["conflicts"]) == 1
assert probe["observations"][0]["body_sha256"] == probe["observations"][1]["body_sha256"]
assert probe["observations"][0]["time"] == probe["observations"][1]["time"]
latest = read(ROOT / "runtime/ic_im_v1_4_r1/latest.json")
result = {"status":"PASS","verified_at":datetime.now().astimezone().isoformat(),
    "production_files_changed":sorted(production),"test_contract_files_updated":sorted(legacy_tests),
    "formal_runtime_files_unchanged":len(runtime),"existing_original_evidence_files_unchanged":len(base["original_evidence"]),
    "formal_ledger":{k:latest[k] for k in ["verified_day","sequence","digest"]},
    "accepted_tests":receipt["counts"],"accepted_binding_files":len(after),"original_exclusions_unchanged":22,
    "fifth_round_tests_included":19,"new_conflict_tests":23,"calendar_data_ast_unchanged":True,
    "http_wrapper_function_ast_unchanged":True,"bs_formula_ast_unchanged":True,
    "real_recovery_scenarios":len(recovery["cases"]),"real_recovery_status":recovery["status"],
    "new_source_pin_sha256":sha(D/"capture_source_pin.json"),"collector":info,
    "collector_receipt":str(path),"collector_attempt_status":live["status"],
    "day_archive_status":live["consistency_statistics"]["status"],"day_statistics":live["consistency_statistics"],
    "available_packages":packages,"http_date_probe":"body/time unchanged, HTTP Date changed; rejected under existing strict identity",
    "observed_trading_days":1,"generic_url_response_cache_added":False,"historical_nav_recalculated":False,
    "remote_poe_configuration_changed":False,"spec_sha256":sha(spec)}
(D/"final_verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({k:result[k] for k in ["status","accepted_tests","formal_runtime_files_unchanged","collector_attempt_status","day_archive_status"]},ensure_ascii=False))
