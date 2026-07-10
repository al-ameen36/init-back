from __future__ import annotations

import asyncio
import logging

from features.events import (
    complete,
    emit,
)
from features.github import (
    get_primary_languages,
    get_pull_request_stats,
    get_repositories,
    get_total_commits,
    get_total_stars,
    get_user,
)
from features.technologies import extract_developer_packages


logger = logging.getLogger("init")


async def analyze_developer(
    job_id: str,
    username: str,
) -> None:
    """
    Analyze a developer's GitHub profile and stream progress via SSE.
    """

    logger.info("job=%s analysis started for username=%s", job_id, username)

    try:
        # ---------------------------------------------------------
        # Profile
        # ---------------------------------------------------------

        logger.info("job=%s fetching profile", job_id)
        user = await asyncio.to_thread(
            get_user,
            username,
        )

        await emit(
            job_id,
            "profile",
            data={
                "username": username,
                "avatar_url": user.get("avatar_url"),
                "name": user.get("name"),
                "bio": user.get("bio"),
            },
        )

        # ---------------------------------------------------------
        # Repositories
        # ---------------------------------------------------------

        logger.info("job=%s fetching repositories", job_id)
        repositories = await asyncio.to_thread(
            get_repositories,
            username,
        )

        await emit(
            job_id,
            "repositories",
            data={
                "count": len(repositories),
                "repos": [
                    {
                        "name": repo["name"],
                        "language": repo.get("language"),
                        "stars": repo.get("stargazers_count", 0),
                    }
                    for repo in repositories
                ],
            },
        )

        # ---------------------------------------------------------
        # Languages
        # ---------------------------------------------------------

        languages = get_primary_languages(
            repositories,
        )

        await emit(
            job_id,
            "languages",
            data={
                "languages": languages,
            },
        )

        # ---------------------------------------------------------
        # Technologies
        # ---------------------------------------------------------

        logger.info("job=%s extracting technologies", job_id)
        technologies = await asyncio.to_thread(
            extract_developer_packages,
            repositories,
        )

        await emit(
            job_id,
            "technologies",
            data={
                "packages": technologies,
            },
        )

        # ---------------------------------------------------------
        # Pull Requests
        # ---------------------------------------------------------

        logger.info("job=%s fetching pull request stats", job_id)
        pr_stats = await asyncio.to_thread(
            get_pull_request_stats,
            username,
        )
        pr_stats["total_commits"] = await asyncio.to_thread(
            get_total_commits,
            username,
        )

        await emit(
            job_id,
            "pull_requests",
            data=pr_stats,
        )

        # ---------------------------------------------------------
        # Finished
        # ---------------------------------------------------------

        await emit(
            job_id,
            "completed",
            data={
                "username": username,
                "avatar_url": user.get("avatar_url"),
                "name": user.get("name"),
                "bio": user.get("bio"),
                "repositories": len(repositories),
                "stars": get_total_stars(repositories),
                "languages": languages,
                "technologies": technologies,
                "pull_requests": pr_stats,
            },
        )

    except Exception as exc:
        logger.exception("job=%s analysis failed: %s", job_id, exc)
        await emit(
            job_id,
            "error",
            data={
                "message": str(exc),
            },
        )

    finally:
        logger.info("job=%s analysis finished", job_id)
        await complete(job_id)
