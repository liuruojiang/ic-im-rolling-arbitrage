from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path


PRIMARY_MODULES = [
    "ic_monthly_discount_roll_v1",
    "ic_510500_put_mom120_delta_floor_v21",
    "ic_510500_put_ladder180_observation_v22",
    "ic_valuation_overlay_exit_boundary_scan_v4",
    "ic_valuation_overlay_selected_put_sync_v5",
    "im_monthly_discount_roll_v1",
    "im_valuation_window_ladder_scan_v7",
    "im_mo_reconstructed_floor_selection_v14",
    "im_fixed_valuation_overlay_lower_boundary_scan_v17",
    "im_fixed_valuation_overlay_selected_put_sync_v18",
    "im_mo_call_daily_d10_threat_roll_v27",
    "im_put_grid_call_final_audit_v1",
]

EXTRA_ROOT_FILES = [
    "freeze_ic_im_system_mainlines_v1.py",
    "test_freeze_ic_im_system_mainlines_v1.py",
    "audit_option_expiry_semantics_v1.py",
    "test_option_expiry_semantics_audit_v1.py",
    "migrate_ic_im_mainlines_v1.py",
]

EXTRA_DOCS = [
    "new_strategy_test_standard_process.md",
    "ic_im_system_mainlines_v1_spec.md",
    "ic_im_system_mainlines_v1_spec.md.sha256",
    "ic_510500_put_research_mainline_v1.md",
    "ic_510500_put_research_mainline_v1.md.sha256",
    "ic_valuation_overlay_grid_research_mainline_v1.md",
    "ic_valuation_overlay_grid_research_mainline_v1.md.sha256",
    "im_mo_put_research_mainline_v1.md",
    "im_mo_put_research_mainline_v1.md.sha256",
    "im_mo_call_daily_d10_threat5_research_mainline_v1.md",
    "im_mo_call_daily_d10_threat5_research_mainline_v1.md.sha256",
    "im_put_grid_call_final_audit_v1_spec.md",
    "im_put_grid_call_final_audit_v1_spec.md.sha256",
    "im_put_grid_call_final_audit_v1_postrun_audit.md",
    "option_expiry_semantics_audit_v1_plan.md",
]

EXTRA_OUTPUT_DIRS = [
    "ic_put_grid_call_combined_v2",
    "ic_im_system_mainlines_v1",
    "option_expiry_semantics_audit_v1",
]

DATE_AUDIT_INPUTS = [
    "outputs/ic_510500_call_daily_iv_delta_grid_v9/signals.csv",
    "outputs/ic_510500_call_daily_iv_tenor_delta_grid_v10/signals.csv",
    "outputs/ic_510500_call_daily_iv_dte_target_grid_v11/signals.csv",
    "outputs/ic_510500_call_daily_iv_dte60_delta_ladder_v12/signals.csv",
    "outputs/im_mo_close_execution_full_battery_v9/lifecycle_audit.csv",
    "outputs/cyb_etf_option_synthetic_roll_v3/monthly_cycles.csv",
]

EXTRA_USEFUL_TESTS = {
    "test_ic_valuation_overlay_exit_boundary_scan_v4.py",
    "test_im_valuation_window_ladder_scan_v7.py",
    "test_im_mo_call_threat_roll_extended_proxy_v26r1.py",
    "test_im_mo_call_threat_roll_extended_price_proxy_v26r2.py",
    "test_im_mo_call_valuation_threat_roll_v25r2.py",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_module_closure(source: Path) -> set[str]:
    modules = {path.stem: path for path in source.glob("*.py")}
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        if name not in modules:
            raise FileNotFoundError(f"Missing primary/local dependency module: {name}")
        seen.add(name)
        tree = ast.parse(modules[name].read_text(encoding="utf-8-sig"))
        dependencies: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                dependencies.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                dependencies.add(node.module.split(".")[0])
        for dependency in sorted(dependencies & modules.keys()):
            visit(dependency)

    for module in PRIMARY_MODULES:
        visit(module)
    return seen


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    source = Path(args.source).resolve()
    target = Path(args.target).resolve()
    if source == target or source in target.parents:
        raise RuntimeError("Target must be outside the source research workspace")
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"Target must be empty for first migration: {target}")
    target.mkdir(parents=True, exist_ok=True)

    modules = local_module_closure(source)
    keep_tests = {
        f"test_{module}.py"
        for module in modules
        if (source / f"test_{module}.py").exists()
    } | EXTRA_USEFUL_TESTS
    all_ic_im_tests = {
        path.name
        for path in source.glob("test_*.py")
        if path.name.startswith(("test_ic_", "test_im_"))
    }
    obsolete_tests = sorted(all_ic_im_tests - keep_tests)

    copied: list[dict[str, object]] = []

    def copy_file(relative: Path | str, category: str) -> None:
        relative = Path(relative)
        src = source / relative
        dst = target / relative
        if not src.is_file():
            raise FileNotFoundError(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if sha256(src) != sha256(dst):
            raise RuntimeError(f"Copy hash mismatch: {relative}")
        copied.append({
            "category": category,
            "source": str(src),
            "target": str(dst),
            "relative_path": relative.as_posix(),
            "bytes": dst.stat().st_size,
            "sha256": sha256(dst),
        })

    def copy_tree(relative: Path | str, category: str) -> None:
        relative = Path(relative)
        src = source / relative
        if not src.is_dir():
            raise FileNotFoundError(src)
        for path in sorted(item for item in src.rglob("*") if item.is_file()):
            copy_file(path.relative_to(source), category)

    for module in sorted(modules):
        copy_file(f"{module}.py", "runnable_code")
    for name in EXTRA_ROOT_FILES:
        if name not in {f"{module}.py" for module in modules}:
            copy_file(name, "mainline_or_audit_code")
    for name in sorted(keep_tests):
        copy_file(name, "retained_test")

    doc_names: set[str] = set(EXTRA_DOCS)
    for module in modules:
        doc_names.update(path.name for path in (source / "docs").glob(f"{module}*.md*"))
    for name in sorted(doc_names):
        copy_file(Path("docs") / name, "spec_or_audit_doc")

    output_names = {module for module in modules if (source / "outputs" / module).is_dir()}
    output_names.update(EXTRA_OUTPUT_DIRS)
    for name in sorted(output_names):
        copy_tree(Path("outputs") / name, "frozen_output")

    for data_dir in sorted(
        path for path in (source / "data").iterdir()
        if path.is_dir() and path.name.startswith(("ic_", "im_"))
    ):
        copy_tree(data_dir.relative_to(source), "source_data")

    for scan_dir in sorted(path for path in (source / "quant_param_scan_runs").iterdir() if path.is_dir()):
        if any(module in scan_dir.name for module in modules):
            copy_tree(scan_dir.relative_to(source), "selected_parameter_scan")

    for relative in DATE_AUDIT_INPUTS:
        if not (target / relative).exists():
            copy_file(relative, "date_audit_input")

    copy_file("notes/strategy_research_registry.md", "source_registry_archive")
    copied[-1]["relative_path"] = "archive/source_strategy_research_registry_20260820.md"
    original_registry = target / "notes/strategy_research_registry.md"
    archive_registry = target / "archive/source_strategy_research_registry_20260820.md"
    archive_registry.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(original_registry, archive_registry)
    copied[-1]["target"] = str(archive_registry)

    migration_dir = target / "migration"
    migration_dir.mkdir(parents=True, exist_ok=True)
    (migration_dir / "obsolete_tests_to_delete.txt").write_text(
        "\n".join(obsolete_tests) + ("\n" if obsolete_tests else ""), encoding="utf-8"
    )
    summary = {
        "version": "ic_im_mainline_workspace_migration_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(source),
        "target": str(target),
        "primary_modules": PRIMARY_MODULES,
        "local_module_closure_count": len(modules),
        "retained_test_count": len(keep_tests),
        "obsolete_test_count": len(obsolete_tests),
        "copied_file_count": len(copied),
        "copied_bytes": sum(int(item["bytes"]) for item in copied),
        "source_deletion_performed": False,
        "manifest": copied,
    }
    (migration_dir / "migration_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in summary.items() if key != "manifest"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

