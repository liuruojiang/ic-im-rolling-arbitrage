"""Persistent FastAPI/Poe server for the IC/IM v1.4 research surface.

Production must mount ``ICIM_STATE_DIR`` on durable storage.  The in-process
refresh loop advances the hash-chained ledger after each close; successful
close queries also commit through the same observer path.  A missed refresh is
retried on startup and every five minutes, one exchange day at a time.
"""

from __future__ import annotations

import asyncio
import os
import re
import types
from copy import deepcopy
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterable

import fastapi_poe as fp
from fastapi import FastAPI
from fastapi_poe.types import PartialResponse, QueryRequest, SettingsResponse

import poe_ic_im_mainline_v1_4_bot as strategy
import poe_ic_im_v1_4_state as state
from poe_ic_im_v1_4_state import StateStore, anchors_from_record, close_clock


class _CaptureMessage:
    def __init__(self, output: "_CaptureOutput") -> None:
        self.output = output

    def __enter__(self) -> "_CaptureMessage":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def write(self, value: Any) -> None:
        self.output.text.append(str(value))

    def attach_file(self, **kwargs: Any) -> None:
        contents = kwargs.get("contents", b"")
        if hasattr(contents, "read"):
            contents = contents.read()
        if isinstance(contents, str):
            contents = contents.encode("utf-8")
        self.output.attachments.append(
            {
                "name": str(kwargs.get("name", "attachment")),
                "contents": bytes(contents),
                "content_type": str(
                    kwargs.get("content_type", "application/octet-stream")
                ),
                "is_inline": bool(kwargs.get("is_inline", False)),
            }
        )


class _CaptureOutput:
    def __init__(self, query: str) -> None:
        self.text: list[str] = []
        self.attachments: list[dict[str, Any]] = []
        self.query = types.SimpleNamespace(text=query, attachments=[])
        self.default_chat: list[Any] = []

    def update_settings(self, _settings: SettingsResponse) -> None:
        return None

    def start_message(self) -> _CaptureMessage:
        return _CaptureMessage(self)

    class BotError(Exception):
        pass


# Coordinators share strategy module globals (Poe output, observers and anchors).
# A per-store or per-instance lock cannot protect those request-scoped swaps.
_STRATEGY_QUERY_LOCK = strategy.STRATEGY_QUERY_LOCK


class LedgerCoordinator:
    def __init__(self, store: StateStore | None = None) -> None:
        self.store = store or StateStore()
        self.lock = _STRATEGY_QUERY_LOCK
        self.last_refresh_error: str | None = None
        self.last_refresh_diagnostic: str | None = None
        self.evidence_root = Path(os.environ.get("ICIM_EVIDENCE_DIR", str(
            self.store.root / "input_evidence")))
        self.last_evidence_capture: dict[str, Any] | None = None
        self._evidence_capture_bucket: tuple[date, bool] | None = None
        require_migration = os.environ.get("ICIM_REQUIRE_MIGRATION", "0") == "1"
        if require_migration and not self.store.latest_path.is_file():
            raise RuntimeError("部署卷缺少已迁移的v1.4账本，禁止静默创建创世状态")
        self.store.initialize()
        if require_migration:
            migration_path = self.store.root / "migration_record.json"
            if not migration_path.is_file():
                raise RuntimeError("部署卷缺少migration_record.json，禁止启动")
            import json

            migration = json.loads(migration_path.read_text(encoding="utf-8"))
            latest = self.store.load_latest()
            migrated = migration.get("new_ledger", {})
            if (
                str(migrated.get("strategy_revision")) != state.STRATEGY_REVISION
                or str(migrated.get("digest")) != str(self.store.load_sequence(0).get("digest"))
            ):
                raise RuntimeError("迁移证明与当前v1.4账本revision/digest不一致")

    def _record_for_display(self, now: datetime) -> dict[str, Any]:
        latest = self.store.load_latest()
        phase = strategy._market_phase(now)
        try:
            completed = strategy._latest_completed_exchange_day(now)
        except strategy.CalendarCoverageError:
            # Display remains useful when new sessions cannot be certified.
            # The builder and StateStore still reject new unverified signals.
            return latest
        verified = date.fromisoformat(str(latest["verified_day"])[:10])
        live_session = (
            strategy._is_exchange_trading_day(now.date())
            and phase in {"集合竞价", "盘中", "午间休市"}
        )
        if (
            not live_session
            and verified == completed
            and int(latest["sequence"]) > 0
        ):
            return self.store.load_sequence(int(latest["sequence"]) - 1)
        return latest

    def execute_query(
        self,
        query: str,
        now: datetime | None = None,
        *,
        persist_confirmed: bool = True,
        replay_day: date | None = None,
    ) -> tuple[str, list[dict[str, Any]], dict[str, dict[str, Any]]]:
        clock = strategy._beijing_clock(now or strategy._now_beijing())
        with strategy.collection_clock(clock), self.lock:
            record = self._record_for_display(clock)
            strategy.install_runtime_anchors(anchors_from_record(record))
            capture = _CaptureOutput(query)
            observed: dict[str, dict[str, Any]] = {}
            original_poe = strategy.poe
            strategy.poe = capture
            strategy.install_signal_observer(
                lambda product, signal: observed.__setitem__(product, signal)
            )
            try:
                with strategy.evidence_adapter.evidence_location(self.evidence_root), strategy.runtime_clock(clock), strategy.historical_replay(
                    replay_day
                ):
                    strategy.ICIMMainlinesBot().run()
            finally:
                strategy.install_signal_observer(None)
                strategy.poe = original_poe
            if persist_confirmed and set(observed) == {"IC", "IM"} and all(
                item.get("close_confirmed") is True for item in observed.values()
            ):
                stage = "持久化资格校验"
                try:
                    for product, signal in observed.items():
                        state.require_persistence_eligible(signal, product)
                    stage = "持久化"
                    latest = self.store.load_latest()
                    observed_day = next(
                        iter({str(item["market_date"]) for item in observed.values()})
                    )
                    if observed_day > str(latest["verified_day"]):
                        self.store.append_confirmed_signals(latest, observed)
                        self.last_refresh_error = None
                except Exception as exc:  # preserve already-calculated research results.
                    self.last_refresh_error = f"{stage}失败：{type(exc).__name__}: {exc}"
                    message = ("信号已生成，但持久化失败" if stage == "持久化"
                               else "诊断结果已生成，但尚不具备持久化资格")
                    capture.text.append(f"\n\n⚠️ {message}：{exc}。账本未确认推进。\n\n")
            try:
                verified = date.fromisoformat(str(self.store.load_latest()["verified_day"])[:10])
                pending_day = strategy._roll_forward_exchange_day(verified + timedelta(days=1))
                evidence_day = min(pending_day, clock.date())
                archive_status = strategy.evidence_adapter.capture_day_status(self.evidence_root, evidence_day)
                warning = archive_status.get("recovery_warning")
                due = evidence_day < clock.date() or strategy._market_phase(clock) == "收盘后"
                failed = self.last_evidence_capture
                if (not warning and due and archive_status.get("missing_products")
                        and isinstance(failed, dict) and failed.get("status") == "incomplete"
                        and str(failed.get("day")) == str(evidence_day)):
                    missing = "/".join(archive_status["missing_products"])
                    warning = (f"{evidence_day} 当日原日存证缺失（{missing}）；采集在完整封存前失败，"
                               "需另行补证或人工锚定，不能承诺自动恢复。")
                if warning and due:
                    capture.text.append("\n\n⚠️ " + warning + " 当前查询结果与该失败采集会话独立。\n")
            except Exception as exc:
                capture.text.append(f"\n\n⚠️ 原日存证状态暂不可核验：{exc}。\n")
            return "".join(capture.text), capture.attachments, observed

    def capture_current_inputs(self, now: datetime | None = None) -> dict[str, Any]:
        """Prospective evidence collection must continue behind a ledger gap."""
        clock = strategy._beijing_clock(now or strategy._now_beijing())
        if os.environ.get("ICIM_DISABLE_EVIDENCE_CAPTURE") == "1":
            return {"day": str(clock.date()), "status": "disabled"}
        # Capture one early close and one after the official-rate publication
        # window. Failed/incomplete attempts can be retried by the same loop.
        bucket = (clock.date(), clock.time() >= strategy.time(17, 30))
        with self.lock:
            if self._evidence_capture_bucket == bucket:
                return self.last_evidence_capture or {}
            try:
                result = strategy.evidence_adapter.capture_daily(strategy, self.evidence_root, clock)
            except Exception as exc:
                result = {"day": str(clock.date()), "status": "incomplete",
                          "reason": f"{type(exc).__name__}: {exc}"}
            self.last_evidence_capture = result
            if result.get("status") == "captured":
                self._evidence_capture_bucket = bucket
            return result

    def catch_up_once(self, now: datetime | None = None) -> bool:
        clock = strategy._beijing_clock(now or strategy._now_beijing())
        with self.lock:
            self.last_refresh_diagnostic = None
            latest = self.store.load_latest()
            verified = date.fromisoformat(str(latest["verified_day"])[:10])
            completed = strategy._latest_completed_exchange_day(clock)
            if verified >= completed:
                self.last_refresh_error = None
                return False
            next_day = strategy._roll_forward_exchange_day(
                verified + timedelta(days=1)
            )
            try:
                # For today's just-completed session, use the live close path: the
                # exchange's current chain can already contain the complete close
                # while its historical endpoint still lags by one session.  Older
                # missed sessions must remain explicit historical replays.
                same_day_close = next_day == clock.astimezone(strategy.BEIJING).date()
                query_clock = clock if same_day_close else close_clock(next_day)
                replay_day = None if same_day_close else next_day
                text, _, observed = self.execute_query(
                    "信号", query_clock, replay_day=replay_day
                )
                self.last_refresh_diagnostic = (
                    f"诊断交易日：{next_day.isoformat()}\n"
                    f"采集路径：{'current_close' if same_day_close else 'historical_replay'}\n\n"
                    + text
                )
                if set(observed) != {"IC", "IM"}:
                    failures = re.findall(r"(?:完整信号失败|当日信号警告)：([^\n]+)", text)
                    detail = "；".join(failures[:2]) or "未返回逐腿失败摘要"
                    raise RuntimeError(
                        f"自动补账未同时得到IC/IM完整信号｜诊断交易日{next_day}｜{detail}"
                    )
                unresolved = [f"{product}: {signal.get('diagnostic_reason') or signal.get('reason') or signal.get('diagnostic_status') or '信号未完整核验'}"
                              for product, signal in observed.items()
                              if signal.get("diagnostic_only") or signal.get("signal_complete") is False
                              or signal.get("persistence_eligible") is False]
                if unresolved:
                    raise RuntimeError(f"补录 {next_day} 尚未具备落账资格｜" + "；".join(unresolved))
                after = self.store.load_latest()
                after_day = date.fromisoformat(str(after["verified_day"])[:10])
                if (
                    int(after["sequence"]) != int(latest["sequence"]) + 1
                    or after_day != next_day
                    or after["digest"] == latest["digest"]
                ):
                    raise RuntimeError(
                        "自动补账观察到的完整信号未使账本恰好推进一个预期交易日"
                    )
                self.last_refresh_error = None
                return True
            except Exception as exc:  # fail closed; scheduler retries later.
                self.last_refresh_error = f"{type(exc).__name__}: {exc}"
                return False

    def catch_up_until_current(
        self, now: datetime | None = None, *, max_sessions: int = 4
    ) -> int:
        if max_sessions <= 0:
            raise ValueError("max_sessions必须为正")
        clock = strategy._beijing_clock(now or strategy._now_beijing())
        advanced = 0
        while advanced < max_sessions:
            latest = self.store.load_latest()
            verified = date.fromisoformat(str(latest["verified_day"])[:10])
            if verified >= strategy._latest_completed_exchange_day(clock):
                self.last_refresh_error = None
                break
            if not self.catch_up_once(clock):
                break
            advanced += 1
        return advanced

    def health(self, now: datetime | None = None) -> dict[str, Any]:
        with self.lock:
            latest = self.store.load_latest()
            clock = strategy._beijing_clock(now or strategy._now_beijing())
            calendar_error = None
            try:
                completed = strategy._latest_completed_exchange_day(clock)
            except strategy.CalendarCoverageError as exc:
                completed = None
                calendar_error = str(exc)
            verified = date.fromisoformat(str(latest["verified_day"])[:10])
            stale_error = None
            if completed is not None and verified < completed:
                stale_error = f"账本滞后：verified={verified} completed={completed}"
            elif completed is not None and verified > completed:
                stale_error = f"账本日期超前：verified={verified} completed={completed}"
            evidence_error = ("原日证据存档不完整：" + str(self.last_evidence_capture.get("day"))
                              if self.last_evidence_capture and self.last_evidence_capture.get("status") == "incomplete" else None)
            error = self.last_refresh_error or calendar_error or stale_error or evidence_error
            return {
                "status": "ok" if error is None else "degraded",
                "strategy": "IC/IM research signal 1.4",
                "strategy_revision": state.STRATEGY_REVISION,
                "build": strategy.BUILD_ID,
                "verified_day": latest["verified_day"],
                "sequence": latest["sequence"],
                "digest_prefix": str(latest["digest"])[:12],
                "completed_day": completed.isoformat() if completed is not None else None,
                "refresh_error": error,
                "evidence_capture": deepcopy(self.last_evidence_capture),
            }


class PersistentICIMBot(fp.PoeBot):
    def __init__(self, coordinator: LedgerCoordinator, access_key: str | None) -> None:
        super().__init__(access_key=access_key)
        self.coordinator = coordinator

    async def get_response(
        self, request: QueryRequest
    ) -> AsyncIterable[PartialResponse]:
        query = request.query[-1].content if request.query else "查询"
        text, attachments, _ = await asyncio.to_thread(
            self.coordinator.execute_query, query, strategy._now_beijing()
        )
        yield PartialResponse(text=text)
        for attachment in attachments:
            if not self.access_key:
                yield PartialResponse(
                    text=f"\n\n[附件 {attachment['name']} 仅在配置Poe访问密钥后上传]"
                )
                continue
            await self.post_message_attachment(
                message_id=request.message_id,
                file_data=attachment["contents"],
                filename=attachment["name"],
                content_type=attachment["content_type"],
                is_inline=attachment["is_inline"],
            )

    async def get_settings(self, _setting: Any) -> SettingsResponse:
        return strategy._BOT_SETTINGS


coordinator = LedgerCoordinator()
poe_access_key = os.environ.get("POE_ACCESS_KEY")
server_bot = PersistentICIMBot(coordinator, poe_access_key)
async def _refresh_loop() -> None:
    while True:
        try:
            now = strategy._now_beijing()
            if now.time() >= close_clock(now.date()).time():
                try:
                    await asyncio.to_thread(coordinator.catch_up_once, now)
                except Exception as exc:
                    coordinator.last_refresh_error = f"{type(exc).__name__}: {exc}"
                # This runs even when catch-up failed. Cancellation escapes
                # before starting further I/O, and the sole scheduler stays.
                await asyncio.to_thread(coordinator.capture_current_inputs, now)
        except Exception as exc:
            # Calendar/head checks can fail before catch_up_once's replay try.
            # Keep the sole scheduler alive for later retries, without making
            # an unverified session executable. Task cancellation still escapes.
            coordinator.last_refresh_error = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(300)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    task = (
        None
        if os.environ.get("ICIM_DISABLE_INTERNAL_REFRESH") == "1"
        else asyncio.create_task(_refresh_loop())
    )
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


base_app = FastAPI(lifespan=_lifespan)
app: FastAPI = fp.make_app(
    server_bot,
    access_key=poe_access_key or "",
    allow_without_key=not bool(poe_access_key),
    app=base_app,
)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return await asyncio.to_thread(coordinator.health)
