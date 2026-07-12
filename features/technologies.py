from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from collections import Counter

logger = logging.getLogger(__name__)

from features.github import (
    download_file,
    get_repository_contents,
)

# Local wrapper around @specfy/stack-analyser (the maintained, +700 tech
# ruleset), installed in the image at /opt/analyser. We materialize a repo's
# manifests into a temp dir and invoke its Node script.
SPECIFY_NODE = os.environ.get("SPECIFY_NODE", "node")
SPECIFY_SCRIPT = os.environ.get("SPECIFY_SCRIPT", "/opt/analyser/analyse.mjs")

# Technology types that describe a developer's actual stack, as opposed to
# infra / CI / cloud / SaaS noise we don't want surfaced as "skills".
RELEVANT_TYPES = {
    "language",
    "runtime",
    "framework",
    "ui_framework",
    "ui",
    "orm",
    "db",
    "ssg",
    "tool",
}

# Manifests we materialize for the analyser (the package walks the tree and
# inspects these to identify the stack).
SUPPORTED_MANIFESTS = (
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "composer.json",
)


def _call_local_analyser(files: dict[str, str]) -> dict[str, dict] | None:
    """Run the bundled @specfy/stack-analyser (tools/analyse.mjs) over the
    repo manifests materialized into a temp dir.

    Returns a mapping ``{tech_key: {"name": ..., "type": ...}}`` or ``None``
    when the analyser is missing / fails.
    """
    script = SPECIFY_SCRIPT
    if not os.path.exists(script):
        return None

    with tempfile.TemporaryDirectory() as tmp:
        for path, text in files.items():
            dest = os.path.join(tmp, path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(text)

        try:
            proc = subprocess.run(
                [SPECIFY_NODE, script, tmp],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

        if proc.returncode != 0:
            logger.warning("stack-analyser failed: %s", proc.stderr.strip())
            return None

        try:
            data = json.loads(proc.stdout)
        except ValueError:
            return None

    out: dict[str, dict] = {}
    for item in data.get("techs", []):
        key = item.get("key") or item.get("name")
        if key and item.get("name"):
            out[key] = {"name": item["name"], "type": item.get("type")}
    return out


def extract_repository_packages(
    owner: str,
    repo: str,
) -> list[str]:
    """Return the tech names used by a repository.

    Detection is delegated to the maintained @specfy/stack-analyser ruleset
    (run via the bundled local analyser). Returns an empty list when no
    manifests are present or the analyser is unavailable.
    """
    contents = get_repository_contents(owner, repo)

    repo_files: dict[str, str] = {}
    for item in contents:
        if item["name"] not in SUPPORTED_MANIFESTS:
            continue
        try:
            repo_files[item["path"]] = download_file(owner, repo, item["path"])
        except Exception:
            continue

    if not repo_files:
        return []

    techs = _call_local_analyser(repo_files)
    if techs is None:
        return []

    seen: set[str] = set()
    names: list[str] = []
    for info in techs.values():
        if info.get("type") not in RELEVANT_TYPES:
            continue
        name = info.get("name")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def extract_developer_packages(
    repositories: list[dict],
) -> dict[str, int]:

    counter = Counter()

    for repo in repositories:
        full_name = repo["full_name"]

        owner, repo_name = full_name.split("/", 1)

        try:
            for tech in extract_repository_packages(owner, repo_name):
                counter[tech] += 1

        except Exception:
            continue

    return dict(counter)
