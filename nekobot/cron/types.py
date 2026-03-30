"""Cron job data structures."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class CronSchedule:
    """Schedule configuration for a cron job."""

    kind: Literal["cron", "every", "at"]
    expr: str = ""  # cron expression, kind="cron"
    every_seconds: int = 0  # interval, kind="every"
    at_ms: int = 0  # unix timestamp ms, kind="at"
    tz: str | None = None  # IANA timezone, kind="cron"


@dataclass
class CronJob:
    """A scheduled task."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    enabled: bool = True
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    message: str = ""
    channel: str | None = None
    chat_id: str | None = None
    # Runtime state
    next_run_ms: int = 0
    last_run_ms: int = 0
    last_status: str = ""
    last_error: str = ""
    created_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    delete_after_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "schedule": {
                "kind": self.schedule.kind,
                "expr": self.schedule.expr,
                "every_seconds": self.schedule.every_seconds,
                "at_ms": self.schedule.at_ms,
                "tz": self.schedule.tz,
            },
            "message": self.message,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "next_run_ms": self.next_run_ms,
            "last_run_ms": self.last_run_ms,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "created_ms": self.created_ms,
            "delete_after_run": self.delete_after_run,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronJob:
        sched_data = data.get("schedule", {})
        schedule = CronSchedule(
            kind=sched_data.get("kind", "every"),
            expr=sched_data.get("expr", ""),
            every_seconds=sched_data.get("every_seconds", 0),
            at_ms=sched_data.get("at_ms", 0),
            tz=sched_data.get("tz"),
        )
        return cls(
            id=data.get("id", uuid.uuid4().hex[:8]),
            name=data.get("name", ""),
            enabled=data.get("enabled", True),
            schedule=schedule,
            message=data.get("message", ""),
            channel=data.get("channel"),
            chat_id=data.get("chat_id"),
            next_run_ms=data.get("next_run_ms", 0),
            last_run_ms=data.get("last_run_ms", 0),
            last_status=data.get("last_status", ""),
            last_error=data.get("last_error", ""),
            created_ms=data.get("created_ms", 0),
            delete_after_run=data.get("delete_after_run", False),
        )
