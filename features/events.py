from __future__ import annotations

import asyncio
import logging
import uuid

from dataclasses import dataclass, field

from models.developer import AnalysisEvent

logger = logging.getLogger("init")


@dataclass
class Job:
    id: str
    queue: asyncio.Queue[AnalysisEvent] = field(default_factory=asyncio.Queue)
    done: bool = False


_jobs: dict[str, Job] = {}


def create_job() -> Job:
    job = Job(
        id=uuid.uuid4().hex,
    )

    _jobs[job.id] = job
    logger.info("Created job=%s", job.id)

    return job


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


async def emit(
    job_id: str,
    step: str,
    data: dict | None = None,
) -> None:

    job = get_job(job_id)

    if job is None:
        return

    event = AnalysisEvent(
        step=step,
        status="completed",
        data=data or {},
    )

    logger.debug("job=%s emit event=%s", job.id, step)
    await job.queue.put(event)


async def complete(job_id: str) -> None:
    job = get_job(job_id)

    if job is None:
        return

    job.done = True


async def cleanup(job_id: str) -> None:
    _jobs.pop(job_id, None)
    logger.info("Cleaned up job=%s", job_id)


def serialize(event: AnalysisEvent) -> str:
    return event.model_dump_json()
