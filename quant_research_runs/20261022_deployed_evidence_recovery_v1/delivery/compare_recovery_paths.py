"""Compare explicit and automatic replay facts, preserving strict JSON types."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRIAL = HERE.parent / "probe_02"
a = json.loads((TRIAL / "public_explicit_replay/recovered_record.json").read_text(encoding="utf-8"))
b = json.loads((TRIAL / "automatic_recovery/recovered_record.json").read_text(encoding="utf-8"))
excluded = {"signal_calculation_started_at", "signal_generated_at", "fetch_time"}
differences = []


def compare(x, y, path):
    if type(x) is not type(y):
        differences.append({"path": path, "reason": "type"})
        return
    if isinstance(x, dict):
        if set(x) != set(y):
            differences.append({"path": path, "reason": "keys"})
        for key in x.keys() & y.keys():
            compare(x[key], y[key], path + "." + key)
    elif isinstance(x, list):
        if len(x) != len(y):
            differences.append({"path": path, "reason": "length"})
        for i, (u, v) in enumerate(zip(x, y)):
            compare(u, v, f"{path}[{i}]")
    elif x != y:
        differences.append({"path": path, "reason": "value"})


for product in ["IC", "IM"]:
    compare({k: v for k, v in a["signals"][product].items() if k not in excluded},
            {k: v for k, v in b["signals"][product].items() if k not in excluded}, "$.signals." + product)
compare(a["products"], b["products"], "$.products")
result = {"status": "PASS" if not differences else "FAIL", "differences": differences,
          "excluded_calculation_clocks": sorted(excluded),
          "strict_json_types": True, "compared_signal_fields": {p: len(a["signals"][p]) for p in ["IC", "IM"]},
          "complete_product_states_compared": True, "direct_record_digest": a["digest"],
          "automatic_record_digest": b["digest"],
          "comparison_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
(HERE / "recovery_paths_comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(0 if not differences else 1)
