from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from features.auth import get_current_user
from sse_starlette.sse import EventSourceResponse

from features.developer import analyze_developer
from features.events import (
    cleanup,
    create_job,
    get_job,
    serialize,
)

from features.developer_models import (
    AnalyzeDeveloperRequest,
    AnalyzeDeveloperResponse,
)

logger = logging.getLogger("init")

router = APIRouter(
    prefix="/developer",
    tags=["developer"],
    dependencies=[Depends(get_current_user)],
)


@router.post(
    "/analyze",
    response_model=AnalyzeDeveloperResponse,
)
async def analyze(
    request: AnalyzeDeveloperRequest,
):
    logger.info("Starting analysis for username=%s", request.username)
    job = create_job()

    task = asyncio.create_task(
        analyze_developer(
            job.id,
            request.username,
        )
    )
    job.task = task

    logger.info("Created job=%s for username=%s", job.id, request.username)
    return AnalyzeDeveloperResponse(
        job_id=job.id,
    )


@router.get("/events/{job_id}")
async def events(
    job_id: str,
):
    logger.info("SSE connection opened for job=%s", job_id)
    job = get_job(job_id)

    if job is None:
        logger.warning("SSE connection rejected: job=%s not found", job_id)
        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    # Retrieve the task so we can cancel it if the client disconnects.
    # The task was stored on the job object; fall back gracefully if missing.
    async def stream():

        try:
            while True:
                if job.done and job.queue.empty():
                    break

                event = await job.queue.get()
                logger.debug("job=%s streaming event=%s", job.id, event.step)

                yield {
                    "event": event.step,
                    "data": serialize(event),
                }

        finally:
            logger.info("SSE connection closed for job=%s", job.id)
            # Cancel the background task if the client disconnects before
            # the analysis finishes, so we don't orphan work or leak memory.
            if job.task is not None and not job.task.done():
                job.task.cancel()
            await cleanup(job.id)

    return EventSourceResponse(stream())
