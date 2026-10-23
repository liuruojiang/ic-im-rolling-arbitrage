"""Read-only IC/IM input collector; no signals, ledger advancement, or messages.

This is a companion for the existing adapter.capture_daily, not a signal server.
Use --once for one actual-clock attempt or --loop for five-minute retries. The
host must remain running. No server module is imported and no ledger is created.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parent
PIN_SCHEMA = "icim_capture_source_pin_v1"
LOOP_INTERVAL_SECONDS = 300


class SourcePinError(RuntimeError):
    pass


class CaptureAlreadyRunning(RuntimeError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise SourcePinError("source-pin contains duplicate JSON keys")
        result[name] = value
    return result


class SourcePin:
    """Immutable manifest; relative Python paths cannot escape the code root."""
    def __init__(self, path: Path, root: Path = SOURCE_ROOT):
        self.path, self.root = path.resolve(strict=True), root.resolve(strict=True)
        raw = self.path.read_bytes()
        self.manifest_sha256 = hashlib.sha256(raw).hexdigest()
        try:
            data = json.loads(raw, object_pairs_hook=_unique_object)
            if data.get("schema") != PIN_SCHEMA or not isinstance(data.get("files"), dict) or not data["files"]:
                raise SourcePinError("invalid source-pin schema or empty file list")
            self.files = {}
            for name, digest in data["files"].items():
                relative = Path(name)
                if (relative.is_absolute() or ".." in relative.parts or relative.suffix != ".py"
                        or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                    raise SourcePinError("invalid source-pin path or SHA-256")
                resolved = (self.root / relative).resolve(strict=True)
                if not resolved.is_relative_to(self.root) or resolved == self.root:
                    raise SourcePinError("source-pin path escapes the code root")
                normalized = resolved.relative_to(self.root).as_posix()
                if normalized in self.files:
                    raise SourcePinError("source-pin aliases one module more than once")
                self.files[normalized] = digest
        except (ValueError, TypeError, OSError) as exc:
            raise SourcePinError("unable to validate source-pin") from exc
        self.verify()

    def verify(self, loaded=None):
        try:
            if sha(self.path) != self.manifest_sha256:
                raise SourcePinError("source-pin manifest changed after process startup")
            for name, expected in self.files.items():
                path = self.root / name
                if not path.is_file() or sha(path) != expected:
                    raise SourcePinError("source-pin module drift: " + name)
        except OSError as exc:
            raise SourcePinError("source-pin or a pinned module is unreadable") from exc
        if loaded is not None and not set(loaded) <= set(self.files):
            raise SourcePinError("source-pin does not cover loaded modules: " + ",".join(sorted(set(loaded) - set(self.files))))


def loaded_sources(root: Path = SOURCE_ROOT) -> dict[str, str]:
    # Follow the entry point's actual local import graph rather than inferring
    # all dependencies from their names. In particular im_put_policy is a real
    # dependency whose name does not begin with ic_im_/poe_ic_im_. Intersecting
    # with loaded module locations avoids treating unrelated pytest/research
    # modules as runtime dependencies merely because they share this workspace.
    root = root.resolve(strict=True)
    dependencies, pending = set(), [Path(__file__).resolve()]
    while pending:
        path = pending.pop()
        if path in dependencies:
            continue
        dependencies.add(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (OSError, SyntaxError, UnicodeError) as exc:
            raise SourcePinError("cannot enumerate local production imports") from exc
        for node in ast.walk(tree):
            names = ([alias.name.split(".")[0] for alias in node.names]
                     if isinstance(node, ast.Import) else
                     [node.module.split(".")[0]] if isinstance(node, ast.ImportFrom) and node.module else [])
            for name in names:
                candidate = root / (name + ".py")
                if candidate.is_file():
                    candidate = candidate.resolve(strict=True)
                    if candidate.is_relative_to(root) and candidate not in dependencies:
                        pending.append(candidate)
    paths = {Path(__file__).resolve()}
    for name, module in list(sys.modules.items()):
        location = getattr(module, "__file__", None)
        if location:
            path = Path(location).resolve()
            if (path.suffix == ".py" and path.is_relative_to(root)
                    and (path in dependencies or name.startswith(("ic_im_", "poe_ic_im_")))):
                paths.add(path)
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(paths)}


def validate_locations(state_dir: Path, evidence_dir: Path, workspace: Path = SOURCE_ROOT):
    state_path = state_dir.resolve(strict=True)
    output_path = evidence_dir.resolve()
    workspace = workspace.resolve(strict=True)
    if not state_path.is_dir() or not (state_path / "latest.json").is_file():
        raise RuntimeError("capture requires an existing ledger; initialization is forbidden")
    if not output_path.is_relative_to(workspace) or output_path == workspace:
        raise RuntimeError("evidence-dir must be an explicit directory inside this workspace")
    if output_path.is_relative_to(state_path) or state_path.is_relative_to(output_path):
        raise RuntimeError("evidence-dir and state-dir must be separate trees")
    return state_path, output_path


@contextmanager
def capture_lock(evidence_dir: Path):
    """One process owns an evidence tree, including all daemon idle intervals."""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stream = (evidence_dir / ".capture.lock").open("a+b")
    acquired = False
    try:
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise CaptureAlreadyRunning("another capture process owns this evidence-dir") from exc
        yield
    finally:
        try:
            if acquired:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def ledger_hashes(state_dir: Path) -> dict[str, str]:
    paths = [state_dir / "latest.json", state_dir / "migration_record.json"]
    paths += list((state_dir / "journal").glob("*.json"))
    return {p.relative_to(state_dir).as_posix(): sha(p) for p in sorted(paths) if p.is_file()}


def load_ledger_readonly(state, state_dir: Path):
    """StateStore.load_latest reads/validates the full chain without initializing."""
    if not (state_dir / "latest.json").is_file() or not (state_dir / "migration_record.json").is_file():
        raise RuntimeError("existing ledger and migration proof are required; no bootstrap is allowed")
    store = state.StateStore(state_dir)
    latest = store.load_latest()
    migration = json.loads((state_dir / "migration_record.json").read_text(encoding="utf-8-sig"))
    genesis = store.load_sequence(0)
    proof = migration.get("new_ledger", {})
    if (proof.get("digest") != genesis["digest"] or proof.get("strategy_revision") != state.STRATEGY_REVISION
            or proof.get("schema_version") != state.SCHEMA_VERSION or proof.get("sequence") != 0):
        raise RuntimeError("migration proof is not bound to this schema/revision/genesis")
    return latest, state.anchors_from_record(latest)


class CaptureRunner:
    def __init__(self, state_dir: Path, evidence_dir: Path, source_pin: Path | None = None,
                 *, workspace: Path = SOURCE_ROOT):
        self.state_dir, self.evidence_dir = validate_locations(state_dir, evidence_dir, workspace)
        self.pin = SourcePin(source_pin) if source_pin is not None else None
        self.sources = None
        self.completed_bucket = None
        self.completed_result = None

    def _verify_sources(self):
        try:
            current = loaded_sources()
        except OSError as exc:
            raise SourcePinError("loaded runtime source is unreadable") from exc
        if self.sources is None:
            self.sources = current
        elif any(current.get(name) != expected for name, expected in self.sources.items()):
            raise SourcePinError("loaded runtime source changed; stop and restart from a verified release")
        else:
            self.sources.update(current)
        if self.pin is not None:
            self.pin.verify(current)
        return current

    def cycle(self, clock=None):
        # Delayed imports keep --help and source-pin preflight free of strategy
        # side effects. The server/Coordinator and all writers are absent here.
        if self.pin is not None:
            self.pin.verify()
        import poe_ic_im_mainline_v1_4_bot as strategy
        import poe_ic_im_v1_4_state as state
        import ic_im_evidence_adapter_v1 as adapter
        import ic_im_signal_evidence_v1 as evidence
        clock = strategy._beijing_clock(clock or datetime.now(strategy.BEIJING))
        result = {"schema": "icim_capture_runner_receipt_v1", "started_at": clock.isoformat(),
                  "pid": os.getpid(), "ledger_write_requested": False, "message_send_requested": False,
                  "state_dir": str(self.state_dir), "evidence_dir": str(self.evidence_dir)}
        before = ledger_hashes(self.state_dir)
        try:
            result["source_sha256"] = self._verify_sources()
            result["source_pin_sha256"] = self.pin.manifest_sha256 if self.pin is not None else None
            latest, anchors = load_ledger_readonly(state, self.state_dir)
            result["ledger"] = {"verified_day": latest["verified_day"], "sequence": latest["sequence"], "digest": latest["digest"]}
            strategy._require_official_exchange_calendar(clock.date(), "独立存证采集日")
            if not strategy._is_exchange_trading_day(clock.date()):
                result.update(status="waiting_non_trading_day")
            elif strategy._market_phase(clock) != "收盘后":
                result.update(status="waiting_for_close")
            else:
                bucket = (clock.date(), clock.time() >= strategy.time(17, 30))
                if self.completed_bucket == bucket:
                    result.update(status="already_captured", capture=self.completed_result)
                else:
                    with strategy.STRATEGY_QUERY_LOCK:
                        original = deepcopy(strategy.LIVE_CONTINUATION_ANCHOR)
                        try:
                            strategy.install_runtime_anchors(anchors)
                            with strategy.collection_clock(clock), strategy._network_budget(
                                    2 * strategy.SIGNAL_PRODUCT_NETWORK_BUDGET_SECONDS + 1), \
                                    evidence.publication_guard(self._verify_sources):
                                capture = adapter.capture_daily(strategy, self.evidence_dir, clock)
                        finally:
                            strategy.LIVE_CONTINUATION_ANCHOR.clear()
                            strategy.LIVE_CONTINUATION_ANCHOR.update(original)
                    result.update(status=capture["status"], capture=capture)
                    if capture["status"] == "captured":
                        self.completed_bucket, self.completed_result = bucket, capture
            result["consistency_statistics"] = adapter.capture_day_status(self.evidence_dir, clock.date())
        except SourcePinError:
            raise
        except strategy.CalendarCoverageError as exc:
            result.update(status="waiting_for_official_calendar", error_type=type(exc).__name__, error=str(exc)[:1000])
        except Exception as exc:
            result.update(status="incomplete", error_type=type(exc).__name__, error=str(exc)[:1000])
        finally:
            # A source failure discovered while sealing a session must also
            # stop the collector if a provider/adapter handled that exception.
            self._verify_sources()
        after = ledger_hashes(self.state_dir)
        result["ledger_paths_and_bytes_unchanged"] = before == after
        if before != after:
            result.update(status="incomplete", ledger_observation="ledger changed during collection; collector does not advance it")
            self.completed_bucket = None
        result["finished_at"] = datetime.now(strategy.BEIJING).isoformat()
        return result

    def write_receipt(self, result):
        day = str(result["started_at"])[:10]
        folder = self.evidence_dir / "capture_receipts" / day
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (datetime.now().strftime("%H%M%S%f") + "-" + uuid.uuid4().hex + ".json")
        with target.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        return target

    def run(self, *, loop=False, max_rounds=None, wait=time.sleep):
        rounds = 0
        with capture_lock(self.evidence_dir):
            while True:
                try:
                    result = self.cycle()
                except SourcePinError as exc:
                    result = {"schema": "icim_capture_runner_receipt_v1", "status": "source_pin_mismatch",
                              "started_at": datetime.now().astimezone().isoformat(), "pid": os.getpid(),
                              "ledger_write_requested": False, "message_send_requested": False,
                              "error_type": type(exc).__name__, "error": str(exc)[:1000]}
                path = self.write_receipt(result)
                print(json.dumps({"status": result["status"], "receipt": str(path), "pid": os.getpid()}, ensure_ascii=False), flush=True)
                rounds += 1
                if result["status"] == "source_pin_mismatch":
                    return 2
                if not loop or (max_rounds is not None and rounds >= max_rounds):
                    return 1 if result["status"] == "incomplete" else 0
                wait(LOOP_INTERVAL_SECONDS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="one actual-clock capture attempt")
    mode.add_argument("--loop", action="store_true", help="retry every five minutes while this host remains running")
    parser.add_argument("--state-dir", type=Path, required=True, help="existing migrated schema4/r1 ledger; read-only")
    parser.add_argument("--evidence-dir", type=Path, required=True, help="separate output directory inside this workspace")
    parser.add_argument("--source-pin", type=Path, help="immutable icim_capture_source_pin_v1 manifest")
    parser.add_argument("--max-rounds", type=int, help="optional finite --loop rounds for controlled verification")
    args = parser.parse_args(argv)
    if args.max_rounds is not None and (not args.loop or args.max_rounds < 1):
        parser.error("--max-rounds requires --loop and a positive integer")
    try:
        return CaptureRunner(args.state_dir, args.evidence_dir, args.source_pin).run(loop=args.loop, max_rounds=args.max_rounds)
    except CaptureAlreadyRunning as exc:
        print(json.dumps({"status": "already_running", "error": str(exc)}, ensure_ascii=False), flush=True)
        return 3
    except Exception as exc:
        print(json.dumps({"status": "startup_blocked", "error_type": type(exc).__name__, "error": str(exc)[:1000]}, ensure_ascii=False), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
