"""Tests for the cron module."""

import asyncio
import time
from pathlib import Path

import pytest

from nekobot.bus.queue import MessageBus
from nekobot.cron.types import CronJob, CronSchedule
from nekobot.cron.store import CronStore
from nekobot.cron.service import CronService


# ---- types.py ----


class TestCronJobSerialization:
    def test_round_trip(self):
        job = CronJob(
            id="abc123",
            name="test",
            schedule=CronSchedule(kind="cron", expr="0 8 * * *", tz="Asia/Shanghai"),
            message="hello",
            channel="telegram",
            chat_id="12345",
        )
        data = job.to_dict()
        restored = CronJob.from_dict(data)
        assert restored.id == "abc123"
        assert restored.name == "test"
        assert restored.schedule.kind == "cron"
        assert restored.schedule.expr == "0 8 * * *"
        assert restored.schedule.tz == "Asia/Shanghai"
        assert restored.message == "hello"
        assert restored.channel == "telegram"

    def test_defaults(self):
        job = CronJob()
        assert len(job.id) == 8
        assert job.enabled is True
        assert job.schedule.kind == "every"
        assert job.created_ms > 0


# ---- store.py ----


class TestCronStore:
    def test_empty_load(self, tmp_path: Path):
        store = CronStore(tmp_path / "cron" / "jobs.json")
        assert store.load() == []

    def test_add_and_load(self, tmp_path: Path):
        store = CronStore(tmp_path / "jobs.json")
        job = CronJob(id="j1", name="test", message="hi")
        store.add(job)
        jobs = store.load()
        assert len(jobs) == 1
        assert jobs[0].id == "j1"

    def test_remove(self, tmp_path: Path):
        store = CronStore(tmp_path / "jobs.json")
        store.add(CronJob(id="j1", name="a"))
        store.add(CronJob(id="j2", name="b"))
        assert store.remove("j1") is True
        assert store.remove("j999") is False
        assert len(store.load()) == 1
        assert store.load()[0].id == "j2"

    def test_get(self, tmp_path: Path):
        store = CronStore(tmp_path / "jobs.json")
        store.add(CronJob(id="j1", name="test"))
        assert store.get("j1").name == "test"
        assert store.get("missing") is None

    def test_update(self, tmp_path: Path):
        store = CronStore(tmp_path / "jobs.json")
        job = CronJob(id="j1", name="old")
        store.add(job)
        job.name = "new"
        store.update(job)
        assert store.get("j1").name == "new"


# ---- service.py ----


class TestCronServiceScheduling:
    def test_compute_next_run_every(self, tmp_path: Path):
        store = CronStore(tmp_path / "jobs.json")
        bus = MessageBus()
        service = CronService(store, bus)

        job = CronJob(schedule=CronSchedule(kind="every", every_seconds=60))
        service._compute_next_run(job)
        now_ms = int(time.time() * 1000)
        assert job.next_run_ms > now_ms
        assert job.next_run_ms <= now_ms + 61_000

    def test_compute_next_run_at(self, tmp_path: Path):
        store = CronStore(tmp_path / "jobs.json")
        bus = MessageBus()
        service = CronService(store, bus)

        future_ms = int(time.time() * 1000) + 3_600_000
        job = CronJob(schedule=CronSchedule(kind="at", at_ms=future_ms))
        service._compute_next_run(job)
        assert job.next_run_ms == future_ms

    def test_compute_next_run_cron(self, tmp_path: Path):
        store = CronStore(tmp_path / "jobs.json")
        bus = MessageBus()
        service = CronService(store, bus)

        job = CronJob(schedule=CronSchedule(kind="cron", expr="* * * * *"))
        service._compute_next_run(job)
        now_ms = int(time.time() * 1000)
        assert job.next_run_ms > now_ms
        # Next minute, should be within 61 seconds
        assert job.next_run_ms <= now_ms + 61_000


@pytest.mark.asyncio
async def test_fire_publishes_inbound(tmp_path: Path):
    store = CronStore(tmp_path / "jobs.json")
    bus = MessageBus()
    service = CronService(store, bus)

    job = CronJob(
        id="j1",
        name="test",
        message="do stuff",
        channel="telegram",
        chat_id="123",
    )
    await service._fire(job)

    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
    assert msg.content == "do stuff"
    assert msg.channel == "telegram"
    assert msg.chat_id == "123"
    assert msg.sender_id == "cron"
    assert msg.session_key == "cron:j1"
    assert msg.metadata["is_cron"] is True


@pytest.mark.asyncio
async def test_fire_without_channel(tmp_path: Path):
    store = CronStore(tmp_path / "jobs.json")
    bus = MessageBus()
    service = CronService(store, bus)

    job = CronJob(id="j2", name="test", message="hello")
    await service._fire(job)

    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
    assert msg.channel == "cron"
    assert msg.chat_id == "j2"
