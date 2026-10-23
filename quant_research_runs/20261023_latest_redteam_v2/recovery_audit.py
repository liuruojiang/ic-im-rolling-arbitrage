"""Real deployed inputs; isolated server recovery and destructive-copy negatives."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import traceback

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_date(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=_json_date) + "\n", encoding="utf-8")


def hashes(root, pattern="*"):
    return {p.relative_to(root).as_posix(): sha(p) for p in root.rglob(pattern) if p.is_file()}


def copy_ledger(target):
    shutil.copytree(ROOT / "runtime/ic_im_v1_4_r1", target)
    return target


def main():
    trial = HERE / sys.argv[1]
    trial.mkdir(exist_ok=False)
    actual = datetime.now().astimezone()
    import_state = copy_ledger(trial / "import_ledger")
    os.environ.update(
        ICIM_STATE_DIR=str(import_state), ICIM_REQUIRE_MIGRATION="1",
        ICIM_DISABLE_INTERNAL_REFRESH="1", ICIM_DISABLE_EVIDENCE_CAPTURE="1",
        ICIM_EVIDENCE_DIR=str(ROOT / "quant_research_runs/20261023_multiround_adversarial_v1/selected_original_bundle"),
    )
    import requests
    network = []
    active_case = ["import"]

    def offline(_self, method, url, *args, **kwargs):
        network.append({"case": active_case[0], "method": method, "url": str(url),
                        "stack": [{"file": Path(f.filename).name, "function": f.name, "line": f.lineno}
                                  for f in traceback.extract_stack(limit=10)[:-1]]})
        raise requests.ConnectionError("audit blocks external HTTP; original evidence only")

    requests.sessions.Session.request = offline
    import poe_ic_im_mainline_v1_4_bot as bot
    import poe_ic_im_v1_4_state as state
    import ic_im_evidence_adapter_v1 as adapter
    import ic_im_signal_evidence_v1 as evidence
    server = importlib.import_module("poe_ic_im_v1_4_server")
    archive = ROOT / "quant_research_runs/20261023_multiround_adversarial_v1/selected_original_bundle"
    day = date(2026, 10, 22)  # Archived real-market day; not a current market claim.
    original = state.StateStore(import_state).load_latest()
    assert original["verified_day"] == str(day - timedelta(days=1)), original["verified_day"]
    selected = {product: adapter.load_original(bot, archive, product, day)[0] for product in ["IC", "IM"]}
    source_sha = {n: sha(ROOT / n) for n in json.loads((
        ROOT / "quant_research_runs/20261022_free_source_integration_v1/delivery/capture_source_pin.json"
    ).read_text(encoding="utf-8"))["files"]}
    formal = hashes(ROOT / "runtime")
    archive_before = {p.relative_to(archive).as_posix(): sha(p) for p in archive.rglob("*")
                      if p.is_file() and p.name != ".capture.lock"}
    controlled_clock = datetime.combine(day + timedelta(days=1), time(8), bot.BEIJING)
    report = {"started_at": actual.isoformat(), "synthetic_market_inputs": False,
              "formal_runtime_write": False, "market_day": str(day),
              "automatic_scheduler_control_clock": controlled_clock.isoformat(),
              "scheduler_clock_is_simulated": True, "source_sha256": source_sha,
              "driver_sha256": sha(Path(__file__)), "selected_packages": {
                  k: {"manifest": str(r.manifest_path), "sha256": r.manifest_sha256,
                      "typed_inputs": len(r.context["entries"]), "raw_responses": len(r.context["raw"])}
                  for k, r in selected.items()}, "cases": []}

    def bundle(target, missing_ic=False, corrupt_ic=False):
        shutil.copytree(archive / "blobs", target / "blobs")
        for product, reader in selected.items():
            if missing_ic and product == "IC":
                continue
            dest = target / reader.manifest_path.relative_to(archive)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(reader.manifest_path, dest)
        if corrupt_ic:
            entry = next(e for e in selected["IC"].context["entries"].values() if e["role"] == "market.live")
            blob = evidence._blob_path(target, entry)
            blob.write_bytes(b"deliberate corruption in isolated research copy")

    def run_case(name, *, automatic=False, negative=None):
        active_case[0] = name
        folder = trial / name
        folder.mkdir()
        ledger = copy_ledger(folder / "ledger")
        case_archive = folder / "evidence"
        if negative == "wrong_directory":
            case_archive.mkdir()
        elif negative:
            bundle(case_archive, missing_ic=negative == "missing_ic", corrupt_ic=negative == "corrupt_ic")
        else:
            case_archive = archive
        os.environ["ICIM_EVIDENCE_DIR"] = str(case_archive)
        store = state.StateStore(ledger)
        owner = server.LedgerCoordinator(store)
        assert owner.evidence_root.resolve() == case_archive.resolve()
        before = store.load_latest()
        bytes_before = hashes(ledger, "*.json")
        index = len(network)
        result = {"name": name, "evidence_dir": str(case_archive), "negative": negative,
                  "before": {k: before[k] for k in ["verified_day", "sequence", "digest"]}}
        try:
            if automatic or negative:
                advanced = owner.catch_up_until_current(controlled_clock, max_sessions=2)
                text = owner.last_refresh_diagnostic or ""
                observed = store.load_latest().get("signals", {}) if advanced else {}
                result["advanced_sessions"] = advanced
            else:
                text, attachments, observed = owner.execute_query("信号", actual, replay_day=day)
                result["attachments"] = len(attachments)
            (folder / "response.txt").write_text(text, encoding="utf-8")
            after = store.load_latest()
            result["after"] = {k: after[k] for k in ["verified_day", "sequence", "digest"]}
            result["refresh_error"] = owner.last_refresh_error
            result["network_attempts"] = network[index:]
            if negative:
                assert after["digest"] == before["digest"] and hashes(ledger, "*.json") == bytes_before
                assert result["advanced_sessions"] == 0 and owner.last_refresh_error
                result["atomic_rejection"] = True
            else:
                assert after["verified_day"] == str(day) and after["sequence"] == before["sequence"] + 1
                assert after["previous_digest"] == before["digest"]
                assert set(observed) == {"IC", "IM"}
                for product, signal in observed.items():
                    state.require_persistence_eligible(signal, product)
                    info = signal["original_evidence"]
                    assert info["manifest_sha256"] == selected[product].manifest_sha256
                    assert info["unrecorded_inputs"] == []
                    assert not signal.get("historical_evidence_supplements")
                result["signals"] = {k: {x: v.get(x) for x in [
                    "signal_complete", "persistence_eligible", "diagnostic_only", "calendar_provisional",
                    "market_date", "signal_generation_timing", "original_evidence"]} for k, v in observed.items()}
                write(folder / "recovered_record.json", after)
                again_before = hashes(ledger, "*.json")
                assert owner.catch_up_until_current(controlled_clock, max_sessions=2) == 0
                assert hashes(ledger, "*.json") == again_before
                result["idempotent"] = True
                result["health"] = owner.health(controlled_clock)
                assert result["health"]["status"] == "ok"
                assert not result["network_attempts"], "server rendering attempted external HTTP outside stored inputs"
                result["zero_external_http"] = True
            result["status"] = "PASS"
        except Exception as exc:
            result.update(status="FAIL", error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
            result["network_attempts"] = network[index:]
        write(folder / "receipt.json", result)
        report["cases"].append(result)
        print(json.dumps({k: result.get(k) for k in ["name", "status", "error", "advanced_sessions"]}, ensure_ascii=False), flush=True)

    for name, options in [
        ("public_explicit_replay", {}),
        ("automatic_recovery", {"automatic": True}),
        ("missing_ic_package", {"negative": "missing_ic"}),
        ("corrupt_ic_blob", {"negative": "corrupt_ic"}),
        ("wrong_evidence_directory", {"negative": "wrong_directory"}),
    ]:
        run_case(name, **options)
    report["formal_runtime_unchanged"] = hashes(ROOT / "runtime") == formal
    report["source_unchanged"] = all(sha(ROOT / n) == h for n, h in source_sha.items())
    report["existing_archive_bytes_unchanged"] = all((archive / n).is_file() and sha(archive / n) == h for n, h in archive_before.items())
    report["network_attempts"] = network
    report["status"] = "PASS" if all(r["status"] == "PASS" for r in report["cases"]) and all(
        report[k] for k in ["formal_runtime_unchanged", "source_unchanged", "existing_archive_bytes_unchanged"]
    ) else "NOT_ACCEPTED"
    report["finished_at"] = datetime.now().astimezone().isoformat()
    write(trial / "receipt.json", report)
    print(json.dumps({k: report[k] for k in ["status", "formal_runtime_unchanged", "source_unchanged", "existing_archive_bytes_unchanged"]}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
