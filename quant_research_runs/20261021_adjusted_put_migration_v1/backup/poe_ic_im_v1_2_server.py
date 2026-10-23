"""Persistent FastAPI/Poe server for the IC/IM v1.2 research surface.

Production must mount ``ICIM_STATE_DIR`` on durable storage.  The in-process
refresh loop advances the hash-chained ledger after each close; successful
close queries also commit through the same observer path.  A missed refresh is
retried on startup and every five minutes, one exchange day at a time.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import types
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta
from typing import Any, AsyncIterable

import fastapi_poe as fp
from fastapi import FastAPI
from fastapi_poe.types import PartialResponse, QueryRequest, SettingsResponse

import poe_ic_im_mainline_v1_2_bot as strategy
from poe_ic_im_v1_2_state import StateStore, anchors_from_record, close_clock


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


class LedgerCoordinator:
    def __init__(self, store: StateStore | None = None) -> None:
        self.store = store or StateStore()
        self.lock = threading.RLock()
        self.last_refresh_error: str | None = None
        self.store.initialize()

    def _record_for_display(self, now: datetime) -> dict[str, Any]:
        latest = self.store.load_latest()
        phase = strategy._market_phase(now)
        completed = strategy._latest_completed_exchange_day(now)
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
        clock = now or strategy._now_beijing()
        with self.lock:
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
                with strategy.runtime_clock(clock), strategy.historical_replay(
                    replay_day
                ):
                    strategy.ICIMMainlinesBot().run()
            finally:
                strategy.install_signal_observer(None)
                strategy.poe = original_poe
            if persist_confirmed and set(observed) == {"IC", "IM"} and all(
                bool(item.get("close_confirmed")) for item in observed.values()
            ):
                latest = self.store.load_latest()
                observed_day = next(
                    iter({str(item["market_date"]) for item in observed.values()})
                )
                if observed_day > str(latest["verified_day"]):
                    self.store.append_confirmed_signals(latest, observed)
                    self.last_refresh_error = None
            return "".join(capture.text), capture.attachments, observed

    def catch_up_once(self, now: datetime | None = None) -> bool:
        clock = now or strategy._now_beijing()
        with self.lock:
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
                if set(observed) != {"IC", "IM"}:
                    failures = re.findall(r"完整信号失败：([^\n]+)", text)
                    detail = "；".join(failures[:2]) or "未返回逐腿失败摘要"
                    raise RuntimeError(
                        f"自动补账未同时得到IC/IM完整信号｜{detail}"
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
        clock = now or strategy._now_beijing()
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

    def health(self) -> dict[str, Any]:
        with self.lock:
            latest = self.store.load_latest()
            return {
                "status": "ok" if self.last_refresh_error is None else "degraded",
                "strategy": "IC/IM research candidate 1.2",
                "build": strategy.BUILD_ID,
                "verified_day": latest["verified_day"],
                "sequence": latest["sequence"],
                "digest_prefix": str(latest["digest"])[:12],
                "refresh_error": self.last_refresh_error,
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
        now = strategy._now_beijing()
        if now.time() >= close_clock(now.date()).time():
            await asyncio.to_thread(coordinator.catch_up_once, now)
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
