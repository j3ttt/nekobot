"""Asyncio-based cron scheduler with timer + watchdog."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from loguru import logger

from nekobot.bus.events import InboundMessage
from nekobot.cron.types import CronJob

if TYPE_CHECKING:
    from nekobot.bus.queue import MessageBus
    from nekobot.cron.store import CronStore
    from nekobot.gateway.state import StateEmitter

WATCHDOG_INTERVAL = 30  # seconds


class CronService:
    """Manages scheduled jobs: compute next run, arm timers, fire on time."""

    def __init__(self, store: CronStore, bus: MessageBus, state: StateEmitter | None = None) -> None:
        self._store = store
        self._bus = bus
        self._state = state
        self._timer_handle: asyncio.TimerHandle | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        jobs = self._store.load()
        changed = False
        for job in jobs:
            if job.enabled:
                self._compute_next_run(job)
                changed = True
        if changed:
            self._store.save(jobs)
        self._running = True
        self._arm_timer()
        self._watchdog_task = asyncio.create_task(self._watchdog())
        count = sum(1 for j in jobs if j.enabled)
        if count:
            logger.info("CronService started with {} active job(s)", count)

    async def stop(self) -> None:
        self._running = False
        if self._timer_handle:
            self._timer_handle.cancel()
            self._timer_handle = None
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None

    # ---- Public API (called by MCP tool) ----

    def add_job(self, job: CronJob) -> None:
        self._compute_next_run(job)
        self._store.add(job)
        self._arm_timer()
        logger.info("Cron job added: {} ({})", job.name, job.id)

    def remove_job(self, job_id: str) -> bool:
        ok = self._store.remove(job_id)
        if ok:
            self._arm_timer()
            logger.info("Cron job removed: {}", job_id)
        return ok

    def list_jobs(self) -> list[CronJob]:
        return self._store.load()

    def enable_job(self, job_id: str) -> bool:
        job = self._store.get(job_id)
        if not job:
            return False
        job.enabled = True
        self._compute_next_run(job)
        self._store.update(job)
        self._arm_timer()
        return True

    def disable_job(self, job_id: str) -> bool:
        job = self._store.get(job_id)
        if not job:
            return False
        job.enabled = False
        job.next_run_ms = 0
        self._store.update(job)
        self._arm_timer()
        return True

    # ---- Scheduling internals ----

    def _compute_next_run(self, job: CronJob) -> None:
        now_ms = int(time.time() * 1000)
        match job.schedule.kind:
            case "cron":
                try:
                    from croniter import croniter
                    import datetime

                    tz = None
                    if job.schedule.tz:
                        try:
                            import zoneinfo
                            tz = zoneinfo.ZoneInfo(job.schedule.tz)
                        except Exception:
                            pass
                    base = datetime.datetime.now(tz=tz)
                    it = croniter(job.schedule.expr, base)
                    next_dt = it.get_next(datetime.datetime)
                    job.next_run_ms = int(next_dt.timestamp() * 1000)
                except Exception as e:
                    logger.warning("Invalid cron expr '{}' for job {}: {}", job.schedule.expr, job.id, e)
                    job.next_run_ms = 0
            case "every":
                if job.schedule.every_seconds > 0:
                    base = job.last_run_ms if job.last_run_ms else now_ms
                    job.next_run_ms = base + job.schedule.every_seconds * 1000
                    # Don't schedule in the past
                    if job.next_run_ms <= now_ms:
                        job.next_run_ms = now_ms + job.schedule.every_seconds * 1000
            case "at":
                job.next_run_ms = job.schedule.at_ms

    def _arm_timer(self) -> None:
        if self._timer_handle:
            self._timer_handle.cancel()
            self._timer_handle = None

        if not self._running:
            return

        jobs = self._store.load()
        soonest: int | None = None
        for job in jobs:
            if job.enabled and job.next_run_ms > 0:
                if soonest is None or job.next_run_ms < soonest:
                    soonest = job.next_run_ms

        if soonest is None:
            return

        now_ms = int(time.time() * 1000)
        delay_s = max(0, (soonest - now_ms) / 1000)
        loop = asyncio.get_running_loop()
        self._timer_handle = loop.call_later(delay_s, self._on_timer)

    def _on_timer(self) -> None:
        if self._running:
            asyncio.ensure_future(self._check_and_fire())

    async def _watchdog(self) -> None:
        while self._running:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            if self._running:
                await self._check_and_fire()

    async def _check_and_fire(self) -> None:
        now_ms = int(time.time() * 1000)
        jobs = self._store.load()
        fired = False
        to_delete: list[str] = []

        for job in jobs:
            if not job.enabled or job.next_run_ms <= 0:
                continue
            if job.next_run_ms <= now_ms:
                await self._fire(job)
                job.last_run_ms = now_ms
                job.last_status = "ok"
                job.last_error = ""
                if job.delete_after_run:
                    to_delete.append(job.id)
                else:
                    self._compute_next_run(job)
                    # "at" jobs with past timestamp: disable
                    if job.schedule.kind == "at":
                        job.enabled = False
                        job.next_run_ms = 0
                fired = True

        if fired:
            # Remove one-shot jobs, then save
            jobs = [j for j in jobs if j.id not in to_delete]
            self._store.save(jobs)
            self._arm_timer()

    async def _fire(self, job: CronJob) -> None:
        logger.info("Cron firing job '{}' ({})", job.name, job.id)
        if self._state:
            from nekobot.gateway.state import BotState
            await self._state.emit(BotState.working, f"cron:{job.id}")
        await self._bus.publish_inbound(
            InboundMessage(
                channel=job.channel or "cron",
                sender_id="cron",
                chat_id=job.chat_id or job.id,
                content=job.message,
                metadata={"is_cron": True, "cron_job_id": job.id},
                session_key_override=f"cron:{job.id}",
            )
        )
