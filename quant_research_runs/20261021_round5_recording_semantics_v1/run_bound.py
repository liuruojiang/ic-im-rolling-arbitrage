"""Bind a fresh isolated test invocation to code and actual test selection."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import evidence_gate


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bindings():
    paths = set(ROOT.glob("*.py"))
    for directory in (ROOT / "cloud", ROOT / "tests"):
        if directory.exists():
            paths.update(p for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    paths.update(p for p in (ROOT / "docs").glob("*") if p.is_file() and
                 (p.name.endswith("_spec.md") or p.name.endswith(".sha256")))
    paths.update(p for p in HERE.glob("*") if p.is_file() and p.suffix in {".py", ".json", ".ini"})
    paths.update(p for p in ROOT.glob("*") if p.name in {"pytest.ini", "pyproject.toml", "conftest.py"})
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(paths)}


def main():
    stage, argfile = sys.argv[1:]
    target = HERE / stage
    target.mkdir(exist_ok=False)
    args = json.loads(Path(argfile).read_text(encoding="utf-8"))
    before = bindings()
    (target / "source_before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    env = dict(os.environ, ICIM_AUDIT_STAGE=stage)
    result = subprocess.run([sys.executable, str(HERE / "run_checks.py"), *args], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8")
    after = bindings()
    (target / "source_after.json").write_text(json.dumps(after, indent=2), encoding="utf-8")
    receipt = {"exit_code": result.returncode, "source_before_equals_after": before == after,
               "source_files": len(before), "stage": stage}
    try:
        receipt["counts"] = evidence_gate.validate_run(target / "regression.xml", process_exit_code=result.returncode,
                                                     source_before=before, source_after=after)
    except Exception as exc:
        receipt["acceptance_error"] = str(exc)
        xml = ET.parse(target / "regression.xml").getroot() if (target / "regression.xml").exists() else None
        if xml is not None:
            receipt["diagnostic_counts"] = {key: sum(int(s.get(key, 0)) for s in
                ([xml] if xml.tag == "testsuite" else list(xml))) for key in ("tests", "failures", "errors", "skipped")}
    artifacts = ["regression.xml", "regression.txt", "source_before.json", "source_after.json", "test_run.json", "collection.json"]
    receipt["artifacts"] = {name: sha(target / name) for name in artifacts if (target / name).exists()}
    (target / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    if "acceptance_error" not in receipt:
        evidence_gate.validate_receipt(target)
    print(result.stdout[-18000:])
    print(json.dumps({k: v for k, v in receipt.items() if k != "artifacts"}, indent=2))
    raise SystemExit(result.returncode or (1 if "acceptance_error" in receipt else 0))


if __name__ == "__main__":
    main()
