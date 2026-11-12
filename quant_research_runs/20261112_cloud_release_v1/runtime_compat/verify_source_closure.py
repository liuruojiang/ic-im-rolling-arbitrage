"""Independently verify the bounded runtime repair; no production import/run."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform

import requests

HERE = Path(__file__).resolve().parent
ROOT = Path("D:/动量策略/IC和IM滚动套利")
BACKUP = ROOT / "quant_research_runs/20261112_cloud_release_v1/compatibility_backup"
MODULE = "poe_ic_im_mainline_v1_4_bot.py"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


old_manifest_path = ROOT / "docs/ic_im_expert_release_20261112_manifest.json"
new_manifest_path = ROOT / "docs/ic_im_expert_release_20261112_v2_manifest.json"
old_manifest, new_manifest = load(old_manifest_path), load(new_manifest_path)
assert old_manifest["release"] == "20261112-expert-review-v5-r2"
assert new_manifest["release"] == "20261112-expert-review-v5-r3"
assert sha(BACKUP / MODULE) == old_manifest["files"][MODULE]
assert set(old_manifest["files"]) == set(new_manifest["files"])
assert len(new_manifest["files"]) == 31
differences = [name for name in new_manifest["files"]
               if old_manifest["files"][name] != new_manifest["files"][name]]
assert differences == [MODULE]
binding_before = {name: sha(ROOT / name) for name in new_manifest["files"]}
assert binding_before == new_manifest["files"]
support_before = {name: sha(ROOT / name) for name in new_manifest["support_files"]}
assert support_before == new_manifest["support_files"]

old = ast.parse((BACKUP / MODULE).read_text(encoding="utf-8-sig"))
current = ast.parse((ROOT / MODULE).read_text(encoding="utf-8-sig"))
old_class = next(n for n in old.body if isinstance(n, ast.ClassDef) and n.name == "SignalTransportError")
new_class = next(n for n in current.body if isinstance(n, ast.ClassDef) and n.name == "SignalTransportError")
assert [ast.dump(b, include_attributes=False) for b in old_class.bases] == [
    ast.dump(b, include_attributes=False) for b in new_class.bases]
initializers = [n for n in new_class.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"]
assert len(initializers) == 1
expected_init = ast.parse("def __init__(self, *args, **kwargs):\n    requests.RequestException.__init__(self, *args, **kwargs)\n").body[0]
assert ast.dump(initializers[0], include_attributes=False) == ast.dump(expected_init, include_attributes=False)

# Compile only the exact exception class; avoid all production module startup.
namespace = {"requests": requests}
class_module = ast.Module(body=[new_class], type_ignores=[])
exec(compile(class_module, str(ROOT / MODULE), "exec"), namespace)
exception_class = namespace["SignalTransportError"]
assert issubclass(exception_class, RuntimeError)
assert issubclass(exception_class, requests.ConnectionError)
request_a, request_b = requests.PreparedRequest(), requests.PreparedRequest()
response = requests.Response()
response.request = request_a
inferred = exception_class("controlled inference", response=response)
assert inferred.response is response and inferred.request is request_a
explicit = exception_class("controlled explicit override", request=request_b, response=response)
assert explicit.response is response and explicit.request is request_b
for kind in (RuntimeError, requests.ConnectionError, requests.RequestException):
    try:
        raise explicit
    except kind as caught:
        assert caught is explicit
assert explicit.args == ("controlled explicit override",)

new_class.body.remove(initializers[0])
assert ast.dump(old, include_attributes=False) == ast.dump(current, include_attributes=False)
whole_ast_without_init = hashlib.sha256(ast.dump(current, include_attributes=False).encode()).hexdigest()
requirements = (ROOT / "requirements-poe-v1.4-server.txt").read_text(encoding="utf-8-sig")
old_requirements = (BACKUP / "requirements-poe-v1.4-server.txt").read_text(encoding="utf-8-sig")
assert old_requirements.replace("pandas>=2.1", "pandas==2.3.3") == requirements
assert "pandas==2.3.3" in requirements.splitlines()
assert all(new_manifest["files"][name] == old_manifest["files"][name] == sha(ROOT / name)
           for name in ("ic_im_signal_evidence_v1.py", "ic_im_evidence_adapter_v1.py"))
binding_after = {name: sha(ROOT / name) for name in new_manifest["files"]}
support_after = {name: sha(ROOT / name) for name in new_manifest["support_files"]}
assert binding_before == binding_after and support_before == support_after

result = {
    "status": "passed_bounded_exception_init_and_pandas_pin_source_closure",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "old_manifest_sha256": sha(old_manifest_path),
    "new_manifest_sha256": sha(new_manifest_path),
    "old_release": old_manifest["release"],
    "new_release": new_manifest["release"],
    "source_files": len(binding_before),
    "changed_execution_modules": differences,
    "whole_core_AST_equal_after_removing_only_new_initializer": True,
    "core_AST_without_initializer_sha256": whole_ast_without_init,
    "all_strategy_calendar_price_selection_and_failure_classification_AST_unchanged": True,
    "exception_bases_unchanged": ["RuntimeError", "requests.ConnectionError"],
    "initializer_exactly_delegates_requests_RequestException": True,
    "additional_direct_object_controls": {
        "response_request_inference_preserved": True,
        "explicit_request_overrides_response_request": True,
        "RuntimeError_ConnectionError_RequestException_catches_preserved": True,
        "response_identity_and_args_preserved": True
    },
    "pandas_requirement": "pandas==2.3.3",
    "typed_codec_and_adapter_bytes_unchanged": True,
    "playback_assertions_weakened": False,
    "local_runtime": platform.python_version(),
    "local_requests_version": requests.__version__,
    "cloud_python_311_verified_by_this_audit": False,
    "regression_rerun_by_this_audit": False,
    "cloud_followup_required": "Actual Python3.11 cloud execution of original failure nodes and full release gates",
    "binding_before_equals_after": True,
    "product_network_SMTP_or_formal_ledger_operations": False,
    "scope_limits": [
        "Direct exception checks are local object controls, not the real cloud CI result.",
        "Dependency pin resolves the observed version contract; no Pandas3 compatibility is claimed.",
        "Core initializer is the only changed execution AST; separately changed verification runners are outside this core behavior claim."
    ]
}
for name, value in [("binding_before.json", {**binding_before, **support_before}),
                    ("binding_after.json", {**binding_after, **support_after}), ("summary.json", result)]:
    with (HERE / name).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
print(json.dumps({key: result[key] for key in ["status", "source_files", "local_runtime",
    "typed_codec_and_adapter_bytes_unchanged", "cloud_python_311_verified_by_this_audit"]}))
