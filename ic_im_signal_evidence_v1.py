"""Append-only research input evidence, separate from the strategy ledger.

The archive establishes byte integrity and keeps the real acquisition clock. It
does not establish market identity, calendar coverage, or permission to persist a
signal: consumers must run their existing validators when replaying these inputs.
No archive directory is created on import.
"""
from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import math
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np
import pandas as pd

SCHEMA = "ic_im_signal_evidence_v1"
MAX_ITEM_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_SESSION_ITEMS = 2048
MAX_SESSION_FILES = 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")
current_session: contextvars.ContextVar[CaptureSession | None] = contextvars.ContextVar(
    "icim_evidence_capture", default=None
)
current_reader: contextvars.ContextVar[EvidenceReader | None] = contextvars.ContextVar(
    "icim_evidence_reader", default=None
)
_publication_guard: contextvars.ContextVar[Callable[[], Any] | None] = contextvars.ContextVar(
    "icim_evidence_publication_guard", default=None
)


class EvidenceUnavailable(RuntimeError):
    """A required original-day input is missing, invalid, or incomplete."""


def sina_mo_quote_variables(raw: bytes, expected_symbols: list[str]) -> dict[str, tuple[bytes, bytes]]:
    """Parse only complete original Sina MO assignments, never identifier suffixes.

    Each value contains the payload and the exact assignment bytes. The same
    parser is used at capture, direct consumption and batch replay projection;
    integrity-valid older archives do not gain an identity by being projected.
    Whitespace between assignments is allowed, but other text is not ignored.
    """
    symbol_pattern = r"P_OP_mo[0-9]{4}[CP][0-9]+"
    if (not expected_symbols or len(expected_symbols) != len(set(expected_symbols))
            or any(not isinstance(symbol, str) or re.fullmatch(symbol_pattern, symbol) is None
                   for symbol in expected_symbols)):
        raise EvidenceUnavailable("Sina verification batch request identities invalid")
    pattern = re.compile(rb'(?:var[ \t]+)?hq_str_(P_OP_mo[0-9]{4}[CP][0-9]+)[ \t]*=[ \t]*"([^"\r\n]*)"[ \t]*;?')
    values: dict[str, tuple[bytes, bytes]] = {}
    position = 0
    while position < len(raw):
        while position < len(raw) and raw[position] in b" \t\r\n":
            position += 1
        if position == len(raw):
            break
        match = pattern.match(raw, position)
        if match is None:
            raise EvidenceUnavailable("Sina verification batch contains an invalid exact variable identity")
        symbol = match[1].decode("ascii")
        if symbol in values or not match[2]:
            raise EvidenceUnavailable("Sina verification batch identities duplicate or payload empty")
        values[symbol] = (match[2], match[0])
        position = match.end()
        if (not match[0].rstrip().endswith(b";") and position < len(raw)
                and raw[position] not in b"\r\n"):
            raise EvidenceUnavailable("Sina verification batch assignments lack a separator")
    if set(values) != set(expected_symbols):
        raise EvidenceUnavailable("Sina verification batch identities incomplete")
    return values


@contextmanager
def publication_guard(callback: Callable[[], Any]) -> Iterator[None]:
    """Validate the capture source before its manifest can become readable.

    Capture sessions retain their creation scope's callback. Failure seals an
    incomplete archive and remains available as ``session.publication_error``;
    the collector must still perform its final source check and report errors.
    """
    if not callable(callback):
        raise TypeError("evidence publication guard must be callable")
    token = _publication_guard.set(callback)
    try:
        yield
    finally:
        _publication_guard.reset(token)


def _now() -> datetime:
    # Never substitute the strategy/replay clock for actual acquisition time.
    return datetime.now(timezone.utc)


def _aware(value: datetime | str, label: str) -> datetime:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timezone missing")
        return result
    except (ValueError, TypeError, AttributeError) as exc:
        raise EvidenceUnavailable(f"{label} requires a timezone-aware timestamp") from exc


def _day(value: date, label: str = "evidence day") -> date:
    if type(value) is not date:
        raise EvidenceUnavailable(f"{label} must be a date, not a datetime/string")
    return value


def _dumps(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EvidenceUnavailable("duplicate JSON keys in evidence")
        value[key] = item
    return value


def _loads(data: bytes) -> Any:
    try:
        return json.loads(data, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              EvidenceUnavailable(f"nonstandard JSON constant {value}")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise EvidenceUnavailable("invalid evidence JSON") from exc


def _dtype(value: str) -> Any:
    """Only data dtypes, never a Python object/class name supplied by a file."""
    if value in {"Int8", "Int16", "Int32", "Int64", "UInt8", "UInt16", "UInt32",
                 "UInt64", "Float32", "Float64", "boolean", "string"}:
        return value
    if re.fullmatch(r"datetime64\[(?:ns|us|ms|s)(?:, [A-Za-z0-9_+./:-]+)?\]", value):
        return value
    if re.fullmatch(r"timedelta64\[(?:ns|us|ms|s)\]", value):
        return value
    try:
        dtype = np.dtype(value)
        if dtype.kind not in "biufOSU" or dtype.fields is not None or dtype.subdtype is not None:
            raise ValueError("unsupported dtype")
        return dtype
    except (ValueError, TypeError) as exc:
        raise EvidenceUnavailable(f"unsupported evidence dtype: {value}") from exc


def encode(value: Any, _depth: int = 0) -> Any:
    """A tagged, JSON-safe codec; it never uses pickle or executable imports.

    NaN/infinity are preserved as explicit float values so downstream financial
    validators can reject them. They cannot become JSON strings or booleans.
    """
    if _depth > 64:
        raise EvidenceUnavailable("evidence nesting limit exceeded")
    enc = lambda item: encode(item, _depth + 1)
    if value is pd.NA:
        return {"t": "pd.NA"}
    if value is pd.NaT:
        return {"t": "pd.NaT"}
    if isinstance(value, pd.Timestamp):
        return {"t": "timestamp", "v": value.isoformat()}
    if isinstance(value, pd.Timedelta):
        return {"t": "timedelta", "v": value.value}
    if isinstance(value, np.generic):
        if value.dtype.kind not in "biuf" or value.dtype.itemsize > 8:
            raise EvidenceUnavailable(f"unsupported numpy evidence scalar: {value.dtype}")
        return {"t": "numpy", "dtype": value.dtype.str, "v": enc(value.item())}
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        return {"t": "float", "v": value.hex()}
    if isinstance(value, datetime):
        return {"t": "datetime", "v": value.isoformat()}
    if isinstance(value, date):
        return {"t": "date", "v": value.isoformat()}
    if isinstance(value, bytes):
        return {"t": "bytes", "v": base64.b64encode(value).decode("ascii")}
    if isinstance(value, pd.MultiIndex):
        return {"t": "multiindex", "levels": [enc(x) for x in value.levels],
                "codes": [enc(x.tolist()) for x in value.codes], "names": enc(value.names)}
    if isinstance(value, pd.RangeIndex):
        return {"t": "rangeindex", "start": value.start, "stop": value.stop,
                "step": value.step, "name": enc(value.name)}
    if isinstance(value, pd.DatetimeIndex):
        return {"t": "datetimeindex", "v": enc(value.tolist()), "name": enc(value.name),
                "tz": str(value.tz) if value.tz is not None else None,
                "freq": value.freqstr}
    if isinstance(value, pd.Index):
        return {"t": "index", "v": enc(value.tolist()), "dtype": str(value.dtype),
                "name": enc(value.name)}
    if isinstance(value, pd.DataFrame):
        return {"t": "frame", "index": enc(value.index), "columns": enc(value.columns),
                "series": [enc(value.iloc[:, index]) for index in range(len(value.columns))],
                "attrs": enc(value.attrs)}
    if isinstance(value, pd.Series):
        result = {"t": "series", "index": enc(value.index), "name": enc(value.name),
                  "v": enc(value.tolist()), "attrs": enc(value.attrs), "dtype": str(value.dtype)}
        if isinstance(value.dtype, pd.CategoricalDtype):
            result["categories"] = enc(value.cat.categories)
            result["ordered"] = value.cat.ordered
        return result
    if isinstance(value, tuple):
        return {"t": "tuple", "v": [enc(item) for item in value]}
    if isinstance(value, list):
        return [enc(item) for item in value]
    if isinstance(value, dict):
        return {"t": "dict", "v": [[enc(key), enc(item)] for key, item in value.items()]}
    raise EvidenceUnavailable(f"unsupported evidence value: {type(value).__name__}")


def decode(value: Any, _depth: int = 0) -> Any:
    if _depth > 64:
        raise EvidenceUnavailable("evidence nesting limit exceeded")
    dec = lambda item: decode(item, _depth + 1)
    if value is None or type(value) in {str, bool, int}:
        return value
    if isinstance(value, list):
        return [dec(item) for item in value]
    if not isinstance(value, dict) or "t" not in value:
        raise EvidenceUnavailable("untagged evidence value")
    kind = value["t"]
    try:
        if kind == "float":
            return float.fromhex(value["v"])
        if kind == "pd.NA":
            return pd.NA
        if kind == "pd.NaT":
            return pd.NaT
        if kind == "timestamp":
            return pd.Timestamp(value["v"])
        if kind == "timedelta":
            return pd.Timedelta(value["v"], unit="ns")
        if kind == "numpy":
            dtype = np.dtype(value["dtype"])
            if dtype.kind not in "biuf" or dtype.itemsize > 8:
                raise ValueError("unsupported scalar dtype")
            return np.array(dec(value["v"]), dtype=dtype)[()]
        if kind == "datetime":
            return datetime.fromisoformat(value["v"])
        if kind == "date":
            return date.fromisoformat(value["v"])
        if kind == "bytes":
            return base64.b64decode(value["v"], validate=True)
        if kind == "tuple":
            return tuple(dec(item) for item in value["v"])
        if kind == "dict":
            result = {}
            for key, item in value["v"]:
                key = dec(key)
                if key in result:
                    raise ValueError("duplicate decoded keys")
                result[key] = dec(item)
            return result
        if kind == "rangeindex":
            return pd.RangeIndex(value["start"], value["stop"], value["step"], name=dec(value["name"]))
        if kind == "datetimeindex":
            values = dec(value["v"])
            tz = value["tz"]
            result = pd.DatetimeIndex(pd.to_datetime(values, utc=tz is not None), name=dec(value["name"]))
            result = result.tz_convert(tz) if tz is not None else result
            return pd.DatetimeIndex(result, freq=value.get("freq"))
        if kind == "index":
            return pd.Index(dec(value["v"]), dtype=_dtype(value["dtype"]), name=dec(value["name"]))
        if kind == "multiindex":
            return pd.MultiIndex(levels=[dec(x) for x in value["levels"]],
                                 codes=[dec(x) for x in value["codes"]],
                                 names=dec(value["names"]), verify_integrity=True)
        if kind == "series":
            data = dec(value["v"])
            dtype = (pd.CategoricalDtype(categories=dec(value["categories"]), ordered=value["ordered"])
                     if value["dtype"] == "category" else _dtype(value["dtype"]))
            result = pd.Series(data, index=dec(value["index"]), name=dec(value["name"]), dtype=dtype)
            result.attrs = dec(value["attrs"])
            return result
        if kind == "frame":
            columns, index = dec(value["columns"]), dec(value["index"])
            series = [dec(item) for item in value["series"]]
            if len(columns) != len(series) or any(not item.index.equals(index) for item in series):
                raise ValueError("frame shape/index mismatch")
            result = pd.concat(series, axis=1) if series else pd.DataFrame(index=index)
            result.columns = columns
            result.attrs = dec(value["attrs"])
            return result
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError, IndexError) as exc:
        raise EvidenceUnavailable(f"invalid typed evidence value: {kind}") from exc
    raise EvidenceUnavailable(f"unknown evidence value tag: {kind}")


def _role(role: str) -> str:
    if not isinstance(role, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", role):
        raise EvidenceUnavailable("invalid evidence role")
    return role


def _signature(role: str, key: dict[str, Any]) -> tuple[str, Any]:
    _role(role)
    if not isinstance(key, dict):
        raise EvidenceUnavailable("evidence key must be a dictionary")
    if any(not isinstance(item, str) for item in key):
        raise EvidenceUnavailable("evidence request key names must be strings")
    # Sort normal string keys so named argument order cannot alter identity.
    key = dict(sorted(key.items()))
    encoded = encode(key)
    data = _dumps({"role": role, "key": encoded})
    if len(data) > 65536:
        raise EvidenceUnavailable("evidence request key exceeds limit")
    return hashlib.sha256(data).hexdigest(), encoded


def _canonical(path: Path) -> Path:
    resolved = str(path.resolve())
    # Windows may return either spelling while another writer publishes a hard
    # link. Both denote the same path; normalize only the OS extended prefix.
    if os.name == "nt":
        if resolved.startswith("\\\\?\\UNC\\"):
            resolved = "\\\\" + resolved[8:]
        elif resolved.startswith("\\\\?\\"):
            resolved = resolved[4:]
    return Path(resolved)


def _inside(root: Path, path: Path) -> Path:
    path = path.absolute()
    resolved = _canonical(path)
    if not resolved.is_relative_to(root):
        raise EvidenceUnavailable(f"evidence path escapes archive root: {resolved} outside {root}")
    # A linked directory can be swapped or make an apparently safe blob point
    # outside this archive. Archive-owned path components must be ordinary paths.
    relative = path.relative_to(root)
    current = root
    for piece in relative.parts:
        current = current / piece
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise EvidenceUnavailable("symlink in evidence path")
    return path


def _read(path: Path, limit: int) -> bytes:
    try:
        if not path.is_file() or path.stat().st_size > limit:
            raise EvidenceUnavailable("evidence file missing or exceeds limit")
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise EvidenceUnavailable("evidence file exceeds limit")
        return data
    except OSError as exc:
        raise EvidenceUnavailable(f"cannot read evidence file {path.name}: {exc}") from exc


def _publish(root: Path, path: Path, data: bytes) -> None:
    path = _inside(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _inside(root, path.parent / (".pending-" + uuid.uuid4().hex))
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Linking a fully written file publishes atomically and cannot
            # overwrite an existing destination, including on Windows NTFS.
            os.link(temporary, path)
        except FileExistsError:
            if _read(path, len(data)) != data:
                raise EvidenceUnavailable("existing evidence content conflicts")
    except OSError as exc:
        raise EvidenceUnavailable(f"cannot append evidence: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def canonical_http_params(encoded):
    # Request dictionary order is immaterial; value types and sequence order
    # remain explicit. Retain original encoded parameters in the archive.
    def ordered(value):
        if isinstance(value, dict):
            return {k: ordered(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
        if isinstance(value, list):
            return [ordered(v) for v in value]
        if isinstance(value, tuple):
            return tuple(ordered(v) for v in value)
        return value
    return _dumps(encode(ordered(decode(encoded))))


def _http_identity_headers(row):
    headers = row.get("headers", {})
    # This audited SSE endpoint stamps each HTTP message with a new Date even
    # when its market body (including date/time) is unchanged. RFC 9110 6.6.1:
    # Date describes message origination, not the quote observation time. Keep
    # every original header in raw evidence; only identity comparison omits it.
    # Other endpoints and representation metadata remain strictly compared.
    if re.fullmatch(r"https://yunhq\.sse\.com\.cn:32042/v1/sho/list/tstyle/510500_(?:0[1-9]|1[0-2])",
                    row.get("url", "")):
        headers = {name: value for name, value in headers.items() if name.lower() != "date"}
    return _dumps(headers)


def _validate_http_metadata(row):
    # Error statuses are real source evidence too; only malformed protocol
    # metadata is rejected. bool and integral floats are not HTTP status ints.
    if type(row.get("status_code")) is not int or not 100 <= row["status_code"] <= 599:
        raise EvidenceUnavailable("recorded HTTP response status invalid")
    if row.get("encoding") is not None and not isinstance(row["encoding"], str):
        raise EvidenceUnavailable("recorded HTTP response encoding invalid")


def raw_http_conflicts(rows):
    """Report request ambiguity using the same identity as the strict reader."""
    seen, reported, conflicts = {}, set(), []
    for row in rows:
        request = (row.get("url"), canonical_http_params(row.get("params")))
        response = (row.get("sha256"), row.get("status_code"), row.get("encoding"),
                    _http_identity_headers(row))
        if request in seen and seen[request] != response and request not in reported:
            conflicts.append({"role": "raw_http_conflict", "url": row.get("url"),
                "request_signature": hashlib.sha256(_dumps(request[0]) + request[1]).hexdigest(),
                "error": "同一URL/参数录得不同响应；参考结果不落账，须全新会话重试",
                "resolved": False})
            reported.add(request)
        seen.setdefault(request, response)
    return conflicts


class CaptureSession:
    def __init__(self, root: str | Path, day: date, decision_at: datetime, metadata: dict[str, Any], *,
                 max_item_bytes: int = MAX_ITEM_BYTES, max_total_bytes: int = MAX_TOTAL_BYTES):
        self.root = _canonical(Path(root))
        self.day = _day(day)
        self.decision_at = _aware(decision_at, "decision_at")
        if self.decision_at.date() != self.day:
            raise EvidenceUnavailable("decision_at does not belong to evidence day")
        if (isinstance(max_item_bytes, bool) or isinstance(max_total_bytes, bool)
                or not 0 < max_item_bytes <= MAX_ITEM_BYTES
                or not max_item_bytes <= max_total_bytes <= MAX_TOTAL_BYTES):
            raise EvidenceUnavailable("invalid evidence byte limits")
        self.max_item_bytes, self.max_total_bytes = max_item_bytes, max_total_bytes
        self.metadata = encode(metadata)
        self.opened_at = _now().isoformat()
        self.entries: dict[str, Any] = {}
        self.raw: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []
        # Retain object identity until context exit, so an exception already
        # attributed to a source is not duplicated as an unrelated context error.
        self._recorded_exceptions: list[BaseException] = []
        self.total_bytes = 0
        self.manifest_path: Path | None = None
        self.manifest_sha256: str | None = None
        self.context: dict[str, Any] | None = None
        self._finished = False
        self.annotations: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._role_locks: dict[str, Any] = {}
        self._active_role_owners: dict[str, int] = {}
        self._active_fetches = 0
        self._bound_signals = []
        self._publication_guard = _publication_guard.get()
        self.publication_error: Exception | None = None

    def annotate(self, name: str, value: Any) -> None:
        with self._lock:
            self._annotate_locked(name, value)

    def _annotate_locked(self, name: str, value: Any) -> None:
        if self._finished or not isinstance(name, str) or not name or len(name) > 100:
            raise EvidenceUnavailable("invalid or closed evidence annotation")
        encoded = encode(value)
        if len(_dumps(encoded)) > MAX_MANIFEST_BYTES:
            raise EvidenceUnavailable("evidence annotation exceeds limit")
        self.annotations[name] = encoded

    def _blob(self, data: bytes, suffix: str) -> dict[str, Any]:
        with self._lock:
            return self._blob_locked(data, suffix)

    def _blob_locked(self, data: bytes, suffix: str) -> dict[str, Any]:
        if self._finished:
            raise EvidenceUnavailable("capture session already closed")
        if (len(data) > self.max_item_bytes
                or self.total_bytes + len(data) > self.max_total_bytes
                or len(self.entries) + len(self.raw) >= MAX_SESSION_ITEMS):
            raise EvidenceUnavailable("evidence capture budget exceeded")
        digest = hashlib.sha256(data).hexdigest()
        _publish(self.root, self.root / "blobs" / (digest + suffix), data)
        self.total_bytes += len(data)
        return {"sha256": digest, "bytes": len(data), "suffix": suffix}

    def call(self, role: str, key: dict[str, Any], fetcher: Callable[[], Any]) -> Any:
        signature, encoded_key = _signature(role, key)
        with self._lock:
            if self._finished:
                raise EvidenceUnavailable("capture session already closed")
            role_lock = self._role_locks.setdefault(signature, threading.RLock())
        with role_lock:
            with self._lock:
                if self._finished:
                    raise EvidenceUnavailable("capture session already closed")
                if signature in self.entries:
                    return decode(_loads(_verified_blob(self.root, self.entries[signature])))
                nested = self._active_role_owners.get(signature) == threading.get_ident()
                if not nested:
                    self._active_role_owners[signature] = threading.get_ident()
                    self._active_fetches += 1
            if nested:
                # Older provider wrappers may sit below a new aggregate
                # wrapper for this exact role/key. Record the outer validated
                # value once; counting both would leave an orphan byte total.
                return fetcher()
            try:
                # Independent roles may fetch concurrently; only a matching
                # role/key waits. No archive mutation lock is held over I/O.
                value = fetcher()
                with self._lock:
                    data = _dumps(encode(value))
                    entry = self._blob(data, ".json")
                    entry.update(role=role, key=encoded_key, fetched_at=_now().isoformat())
                    self.entries[signature] = entry
                    for failure in self.failures:
                        if failure.get("signature") == signature:
                            failure["resolved"] = True
                return value
            except Exception as exc:
                with self._lock:
                    self.failures.append({"role": role, "key": encoded_key, "signature": signature,
                                          "error_type": type(exc).__name__, "error": str(exc)[:2000],
                                          "at": _now().isoformat(), "resolved": False})
                    self._recorded_exceptions.append(exc)
                raise
            finally:
                with self._lock:
                    self._active_fetches -= 1
                    self._active_role_owners.pop(signature, None)

    def record_http_conflicts(self):
        with self._lock:
            existing = {row.get("request_signature") for row in self.failures
                        if row.get("role") == "raw_http_conflict"}
            for row in raw_http_conflicts(self.raw):
                if row["request_signature"] not in existing:
                    self.failures.append(row)
                    existing.add(row["request_signature"])
            if existing:
                self._update_bound_signals("inconsistent", False)
            return len(existing)

    def bind_signal(self, signal):
        """A decision cannot be persisted before every capture input is sealed."""
        with self._lock:
            if self._finished:
                raise EvidenceUnavailable("capture session already closed")
            eligible = signal.get("persistence_eligible") is True
            self._bound_signals.append((signal, eligible))
            signal["input_evidence_status"] = "recording"
            signal["persistence_eligible"] = False
            self.record_http_conflicts()
        return signal

    def _update_bound_signals(self, status, complete):
        for signal, eligible in self._bound_signals:
            signal["input_evidence_status"] = status
            signal["persistence_eligible"] = bool(complete and eligible)
            if status in {"inconsistent", "incomplete"}:
                note = "⚠️ 本次采集存证不完整或报文不一致；以下仅供参考，不落账，等待全新会话重试；持续失败需另行补证或人工锚定。"
                notes = signal.setdefault("data_notes", [])
                if note not in notes:
                    notes.append(note)

    def raw_response(self, url: str, params: Any, response: Any, *, body: bytes | None = None) -> dict[str, Any]:
        with self._lock:
            return self._raw_response_locked(url, params, response, body=body)

    def _raw_response_locked(self, url: str, params: Any, response: Any, *, body: bytes | None = None) -> dict[str, Any]:
        try:
            body = response.content if body is None else body
            if not isinstance(body, bytes):
                raise EvidenceUnavailable("raw HTTP body must be bytes")
            entry = self._blob(body, ".bin")
            headers = getattr(response, "headers", {})
            entry.update(url=str(url), params=encode(params), fetched_at=_now().isoformat(),
                         status_code=getattr(response, "status_code", None),
                         encoding=getattr(response, "encoding", None),
                         headers={key: str(headers[key]) for key in
                                  ("Date", "Content-Type", "Last-Modified", "ETag") if key in headers})
            self.raw.append(entry)
            self.record_http_conflicts()
            return entry.copy()
        except Exception as exc:
            self.failures.append({"role": "raw_http", "url": str(url),
                                  "error_type": type(exc).__name__, "error": str(exc)[:2000],
                                  "at": _now().isoformat(), "resolved": False})
            self._recorded_exceptions.append(exc)
            raise

    def finish(self) -> Path:
        with self._lock:
            return self._finish_locked()

    def _finish_locked(self) -> Path:
        if self.manifest_path is not None:
            return self.manifest_path
        if self._active_fetches:
            raise EvidenceUnavailable("cannot close evidence while input fetches are active")
        if self._publication_guard is not None:
            try:
                self._publication_guard()
            except Exception as exc:
                # Do this before publishing any manifest: a reader must never
                # observe a complete archive produced by an unverified source.
                self.publication_error = exc
                self.failures.append({"role": "publication_guard", "error_type": type(exc).__name__,
                                      "error": str(exc)[:2000], "at": _now().isoformat(),
                                      "resolved": False})
        invalid = {row.get("raw_index") for row in self.failures
                   if row.get("role") == "raw_http_metadata"}
        for index, row in enumerate(self.raw):
            try:
                _validate_http_metadata(row)
            except EvidenceUnavailable as exc:
                if index not in invalid:
                    self.failures.append({"role": "raw_http_metadata", "raw_index": index,
                                          "url": row.get("url"), "error_type": type(exc).__name__,
                                          "error": str(exc), "at": _now().isoformat(), "resolved": False})
                    invalid.add(index)
        conflicts = self.record_http_conflicts()
        complete = bool(self.entries) and all(x["resolved"] for x in self.failures)
        integrity = "inconsistent" if conflicts else "complete" if complete else "incomplete"
        if self._bound_signals:
            self.annotations["signal_capture_integrity"] = encode({"version": 1, "status": integrity})
        body = {"schema": SCHEMA, "day": self.day.isoformat(),
                "decision_at": self.decision_at.isoformat(), "opened_at": self.opened_at,
                "closed_at": _now().isoformat(), "metadata": self.metadata,
                "entries": self.entries, "raw": self.raw, "failures": self.failures,
                "total_bytes": self.total_bytes,
                "complete": bool(self.entries) and all(x["resolved"] for x in self.failures)}
        if self.annotations:
            body["annotations"] = self.annotations
        digest = hashlib.sha256(_dumps(body)).hexdigest()
        data = _dumps({"body": body, "sha256": digest})
        if len(data) > MAX_MANIFEST_BYTES:
            raise EvidenceUnavailable("evidence manifest exceeds limit")
        path = self.root / "sessions" / self.day.isoformat() / (_now().strftime("%H%M%S%f") + "-" + uuid.uuid4().hex + ".json")
        _publish(self.root, path, data)
        self.manifest_path, self.manifest_sha256, self.context = path, digest, body
        self._finished = True
        self._update_bound_signals(integrity, complete)
        return path


@contextmanager
def capture_session(root: str | Path, day: date, decision_at: datetime,
                    metadata: dict[str, Any], **limits: Any) -> Iterator[CaptureSession]:
    if current_session.get() is not None or current_reader.get() is not None:
        raise EvidenceUnavailable("nested evidence capture/replay contexts are not allowed")
    session = CaptureSession(root, day, decision_at, metadata, **limits)
    token = current_session.set(session)
    try:
        yield session
    except BaseException as exc:
        # Downstream validators can run after call() has stored its input. A
        # failure there must not leave a seemingly complete recovery package.
        if not any(recorded is exc for recorded in session._recorded_exceptions):
            session.failures.append({"role": "capture_context", "error_type": type(exc).__name__,
                                     "error": str(exc)[:2000], "at": _now().isoformat(),
                                     "resolved": False})
        try:
            session.finish()
        except Exception:
            # Preserve the computation's original exception. Explicit callers
            # can inspect manifest_path=None and report the archiving failure.
            pass
        raise
    else:
        session.finish()
    finally:
        current_session.reset(token)


def _blob_path(root: Path, entry: dict[str, Any]) -> Path:
    if (not isinstance(entry.get("sha256"), str) or _SHA.fullmatch(entry["sha256"]) is None
            or entry.get("suffix") not in {".json", ".bin"}
            or type(entry.get("bytes")) is not int or not 0 <= entry["bytes"] <= MAX_ITEM_BYTES):
        raise EvidenceUnavailable("invalid evidence blob reference")
    return _inside(root, root / "blobs" / (entry["sha256"] + entry["suffix"]))


def _verified_blob(root: Path, entry: dict[str, Any]) -> bytes:
    data = _read(_blob_path(root, entry), MAX_ITEM_BYTES)
    if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise EvidenceUnavailable("evidence blob hash or length mismatch")
    return data


class EvidenceReader:
    def __init__(self, root: Path, path: Path, body: dict[str, Any], digest: str):
        self.root = root
        self.manifest_path, self.manifest_sha256 = path, digest
        self.context = body
        self.manifest = body
        self.metadata = decode(body["metadata"])
        self.day = date.fromisoformat(body["day"])
        self.decision_at = _aware(body["decision_at"], "decision_at")
        self.closed_at = _aware(body["closed_at"], "closed_at")
        self.missing_inputs: list[dict[str, Any]] = []

    def call(self, role: str, key: dict[str, Any]) -> Any:
        signature, _ = _signature(role, key)
        entry = self.context["entries"].get(signature)
        if entry is None:
            self.missing_inputs.append({"role": role, "key": key})
            raise EvidenceUnavailable(f"{self.day} original evidence missing role={role}, key={key}")
        return decode(_loads(_verified_blob(self.root, entry)))

    def http_response(self, url: str, params: Any) -> dict[str, Any]:
        matches = [row for row in self.context["raw"]
                   if row.get("url") == url and canonical_http_params(row.get("params")) == canonical_http_params(encode(params))]
        candidates = [{**row, "body": _verified_blob(self.root, row)} for row in matches]
        # Sina accepts a list of symbols. Project one exact original variable
        # from a verified recorded batch; never construct prices or identities.
        if (not matches and url == "https://hq.sinajs.cn/etag.php" and isinstance(params, dict)
                and set(params) == {"list"} and re.fullmatch(r"P_OP_mo[0-9]{4}[CP][0-9]+", str(params["list"]))):
            for row in reversed(self.context["raw"]):
                key = decode(row.get("params"))
                if row.get("url") != url or not isinstance(key, dict) or set(key) != {"list"}:
                    continue
                symbols = str(key["list"]).split(",")
                if params["list"] not in symbols:
                    continue
                _validate_http_metadata(row)
                body = _verified_blob(self.root, row)
                values = sina_mo_quote_variables(body, symbols)
                selected = values[params["list"]][1]
                candidates.append({**row, "body": selected, "projection": "exact_recorded_Sina_variable",
                                   "parent_raw_sha256": row["sha256"]})
        if not candidates:
            self.missing_inputs.append({"role": "http", "url": url, "params": params})
            raise EvidenceUnavailable(f"{self.day} original evidence has no recorded HTTP request: {url}")
        signatures = set()
        for row in candidates:
            _validate_http_metadata(row)
            signatures.add((row["body"], row["status_code"], row.get("encoding"), _http_identity_headers(row)))
        if len(signatures) != 1:
            # URL/parameters do not identify which observation a caller used.
            # Never invent FIFO correspondence or silently choose a later price.
            # Named provider entries retain their separate role/month/day keys.
            self.missing_inputs.append({"role": "http", "url": url, "params": params,
                                        "reason": "ambiguous_recorded_responses", "candidates": len(candidates)})
            raise EvidenceUnavailable(f"{self.day} ambiguous original HTTP responses for {url}")
        return {**candidates[-1], "matching_recordings": len(candidates)}


def has_session(root: str | Path, day: date, metadata_filter: dict[str, Any]) -> bool:
    """Check verified manifest scope, including failed captures, before routing.

    An unreadable manifest remains an error rather than permission to replace
    unknown original evidence. Valid other-product and supplement manifests do
    not disable an independent dated historical provider.
    """
    root, day = _canonical(Path(root)), _day(day)
    directory = _inside(root, root / "sessions" / day.isoformat())
    try:
        paths = list(directory.glob("*.json"))
    except OSError as exc:
        raise EvidenceUnavailable(f"{day} cannot enumerate original evidence") from exc
    if len(paths) > MAX_SESSION_FILES:
        raise EvidenceUnavailable("evidence session count exceeds limit")
    incomplete = False
    for path in paths:
        try:
            envelope = _loads(_read(_inside(root, path), MAX_MANIFEST_BYTES))
            body = envelope["body"]
            if (not isinstance(body, dict) or body.get("schema") != SCHEMA
                    or body.get("day") != day.isoformat()
                    or hashlib.sha256(_dumps(body)).hexdigest() != envelope.get("sha256")):
                raise EvidenceUnavailable("evidence manifest scope/hash invalid")
            metadata = decode(body["metadata"])
            if not isinstance(metadata, dict):
                raise EvidenceUnavailable("invalid evidence metadata")
            if all(metadata.get(k) == v for k, v in metadata_filter.items()):
                valid_http = True
                for row in body.get("raw", []):
                    try:
                        _validate_http_metadata(row)
                    except EvidenceUnavailable:
                        valid_http = False
                if (body.get("complete") is True and isinstance(body.get("failures"), list)
                        and all(row.get("resolved") is True for row in body["failures"])
                        and valid_http and not raw_http_conflicts(body.get("raw", []))):
                    return True
                incomplete = True
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise EvidenceUnavailable("cannot verify original evidence scope") from exc
    if incomplete:
        raise EvidenceUnavailable(f"{day} original evidence capture incomplete; new session required")
    return False


def _load_one(root: Path, path: Path, day: date) -> EvidenceReader:
    envelope = _loads(_read(_inside(root, path), MAX_MANIFEST_BYTES))
    if not isinstance(envelope, dict) or set(envelope) != {"body", "sha256"}:
        raise EvidenceUnavailable("invalid evidence manifest envelope")
    body, digest = envelope["body"], envelope["sha256"]
    if not isinstance(body, dict) or hashlib.sha256(_dumps(body)).hexdigest() != digest:
        raise EvidenceUnavailable("evidence manifest hash mismatch")
    if body.get("schema") != SCHEMA or body.get("day") != day.isoformat():
        raise EvidenceUnavailable("evidence manifest schema/day mismatch")
    decision = _aware(body["decision_at"], "decision_at")
    opened = _aware(body["opened_at"], "opened_at")
    closed = _aware(body["closed_at"], "closed_at")
    if decision.date() != day or closed < opened:
        raise EvidenceUnavailable("evidence timestamp/day mismatch")
    if body.get("complete") is not True or not isinstance(body.get("failures"), list):
        raise EvidenceUnavailable("evidence capture incomplete")
    if any(failure.get("resolved") is not True for failure in body["failures"]):
        raise EvidenceUnavailable("unresolved evidence capture failures")
    entries, raw = body.get("entries"), body.get("raw")
    if (not isinstance(entries, dict) or not entries or not isinstance(raw, list)
            or len(entries) + len(raw) > MAX_SESSION_ITEMS):
        raise EvidenceUnavailable("invalid evidence entry collection")
    if type(body.get("total_bytes")) is not int or not 0 <= body["total_bytes"] <= MAX_TOTAL_BYTES:
        raise EvidenceUnavailable("evidence byte total mismatch or exceeds limit")
    total = 0
    for signature, entry in entries.items():
        role, key = entry.get("role"), decode(entry.get("key"))
        if _signature(role, key)[0] != signature or entry.get("suffix") != ".json":
            raise EvidenceUnavailable("evidence role/key signature mismatch")
        acquired = _aware(entry["fetched_at"], "fetched_at")
        if not opened <= acquired <= closed:
            raise EvidenceUnavailable("evidence acquisition timestamp out of capture bounds")
        _blob_path(root, entry)
        if total + entry["bytes"] > MAX_TOTAL_BYTES:
            raise EvidenceUnavailable("evidence byte total exceeds limit")
        data = _verified_blob(root, entry)
        decode(_loads(data))
        total += len(data)
    for entry in raw:
        if entry.get("suffix") != ".bin":
            raise EvidenceUnavailable("invalid raw evidence suffix")
        _validate_http_metadata(entry)
        acquired = _aware(entry["fetched_at"], "raw fetched_at")
        if not opened <= acquired <= closed:
            raise EvidenceUnavailable("raw evidence acquisition timestamp out of capture bounds")
        _blob_path(root, entry)
        if total + entry["bytes"] > MAX_TOTAL_BYTES:
            raise EvidenceUnavailable("evidence byte total exceeds limit")
        total += len(_verified_blob(root, entry))
    if type(body.get("total_bytes")) is not int or body["total_bytes"] != total or total > MAX_TOTAL_BYTES:
        raise EvidenceUnavailable("evidence byte total mismatch or exceeds limit")
    if raw_http_conflicts(raw):
        raise EvidenceUnavailable("ambiguous original HTTP responses; capture incomplete")
    metadata = decode(body["metadata"])
    if not isinstance(metadata, dict):
        raise EvidenceUnavailable("invalid evidence metadata")
    return EvidenceReader(root, path, body, digest)


def load_session(root: str | Path, day: date, required_roles: Any = None,
                 metadata_filter: dict[str, Any] | None = None,
                 decision_not_after: datetime | None = None) -> EvidenceReader:
    root, day = _canonical(Path(root)), _day(day)
    cutoff = _aware(decision_not_after, "decision_not_after") if decision_not_after is not None else None
    directory = _inside(root, root / "sessions" / day.isoformat())
    try:
        files = list(directory.glob("*.json"))
    except OSError as exc:
        raise EvidenceUnavailable(f"{day} cannot enumerate original evidence") from exc
    if len(files) > MAX_SESSION_FILES:
        raise EvidenceUnavailable(f"{day} evidence session count exceeds limit")
    roles = set(required_roles or [])
    for role in roles:
        _role(role)
    choices, failures = [], []
    for path in files:
        try:
            reader = _load_one(root, path, day)
            if cutoff is not None and reader.decision_at > cutoff:
                continue
            if metadata_filter and any(reader.metadata.get(key) != value for key, value in metadata_filter.items()):
                continue
            missing = roles - {entry["role"] for entry in reader.context["entries"].values()}
            if missing:
                raise EvidenceUnavailable("missing original evidence roles=" + ",".join(sorted(missing)))
            choices.append(reader)
        except (EvidenceUnavailable, ValueError, TypeError, KeyError, AttributeError) as exc:
            failures.append(f"{path.name}: {exc}")
    if not choices:
        detail = "; ".join(failures[:4]) or "no complete session matching requested roles, metadata and decision time"
        raise EvidenceUnavailable(f"{day} original evidence unavailable: {detail}")
    return max(choices, key=lambda reader: (reader.decision_at, reader.closed_at, reader.manifest_path.name))


@contextmanager
def replay_session(reader: EvidenceReader) -> Iterator[EvidenceReader]:
    if current_session.get() is not None or current_reader.get() is not None:
        raise EvidenceUnavailable("nested evidence capture/replay contexts are not allowed")
    token = current_reader.set(reader)
    try:
        yield reader
    finally:
        current_reader.reset(token)


def source(role: str, keyfn: Callable[..., dict[str, Any]]) -> Callable:
    """Decorate an adapter; no context preserves its existing behaviour."""
    _role(role)
    def decorate(fetcher: Callable) -> Callable:
        @wraps(fetcher)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            reader, session = current_reader.get(), current_session.get()
            if reader is None and session is None:
                return fetcher(*args, **kwargs)
            key = keyfn(*args, **kwargs)
            if reader is not None:
                return reader.call(role, key)
            return session.call(role, key, lambda: fetcher(*args, **kwargs))
        return wrapped
    return decorate
