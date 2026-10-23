from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_output_manifest(directory: Path) -> list[str]:
    manifest = json.loads((directory / "output_manifest.json").read_text(encoding="utf-8"))
    entries = manifest.get("files", manifest)
    return [
        name
        for name, metadata in entries.items()
        if not (directory / name).is_file()
        or sha256(directory / name) != metadata["sha256"]
    ]


def main() -> None:
    migration = json.loads((ROOT / "migration/migration_manifest.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    for item in migration["manifest"]:
        path = Path(item["target"])
        if not path.is_file() or sha256(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            failures.append(item["relative_path"])
    output_failures = {
        name: verify_output_manifest(ROOT / "outputs" / name)
        for name in [
            "ic_im_system_mainlines_v1",
            "ic_put_grid_call_combined_v2",
            "im_put_grid_call_final_audit_v1",
            "option_expiry_semantics_audit_v1",
        ]
    }
    state = json.loads((ROOT / "outputs/ic_im_system_mainlines_v1/mainline_state.json").read_text(encoding="utf-8"))
    workspace_manifest = json.loads((ROOT / "migration/workspace_manifest.json").read_text(encoding="utf-8"))
    workspace_failures = [
        name
        for name, metadata in workspace_manifest["files"].items()
        if not (ROOT / name).is_file()
        or (ROOT / name).stat().st_size != metadata["bytes"]
        or sha256(ROOT / name) != metadata["sha256"]
    ]
    checks = {
        "migration_file_hashes": not failures,
        "formal_output_manifests": all(not value for value in output_failures.values()),
        "ic_call_excluded": state["ic"]["call"] == "excluded",
        "im_call_present": "sell_call_d10_iv26_threat5" in state["im"]["components"],
        "im_rescue_date_explicit": state["im"]["rescue_expiry"] == "strictly_later_nearest_listed_expiry_not_calendar_plus_1m",
        "research_only": state["live_approved"] is False,
        "final_workspace_manifest": not workspace_failures,
    }
    result = {
        "passed": all(checks.values()),
        "checks": checks,
        "migration_failures": failures,
        "output_manifest_failures": output_failures,
        "workspace_manifest_failures": workspace_failures,
        "workspace_files": workspace_manifest["file_count"],
        "workspace_bytes": workspace_manifest["total_bytes"],
        "copied_files": migration["copied_file_count"],
        "copied_bytes": migration["copied_bytes"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
