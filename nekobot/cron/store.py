"""JSON file persistence for cron jobs."""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from nekobot.cron.types import CronJob


class CronStore:
    """Load/save cron jobs from a JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> list[CronJob]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text())
            return [CronJob.from_dict(j) for j in raw.get("jobs", [])]
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load cron jobs from {}: {}", self._path, e)
            return []

    def save(self, jobs: list[CronJob]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "jobs": [j.to_dict() for j in jobs]}
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def add(self, job: CronJob) -> None:
        jobs = self.load()
        jobs.append(job)
        self.save(jobs)

    def remove(self, job_id: str) -> bool:
        jobs = self.load()
        before = len(jobs)
        jobs = [j for j in jobs if j.id != job_id]
        if len(jobs) == before:
            return False
        self.save(jobs)
        return True

    def get(self, job_id: str) -> CronJob | None:
        for job in self.load():
            if job.id == job_id:
                return job
        return None

    def update(self, job: CronJob) -> None:
        jobs = self.load()
        for i, j in enumerate(jobs):
            if j.id == job.id:
                jobs[i] = job
                self.save(jobs)
                return
        logger.warning("Job {} not found for update", job.id)
