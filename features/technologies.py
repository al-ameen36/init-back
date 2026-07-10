from __future__ import annotations

import re
from collections import Counter

import json
import tomllib

from features.github import (
    download_file,
    get_repository_contents,
)

# Words that carry no tech meaning on their own and are dropped from labels.
NOISE_WORDS = {
    "start",
    "core",
    "js",
    "ts",
    "cli",
    "plugin",
    "api",
    "sdk",
    "ui",
    "utils",
    "lib",
    "app",
    "the",
    "server",
    "client",
}

# Generic frameworks that are implied by a brand scope and dropped from the
# display label (but kept as matching tokens elsewhere).
GENERIC_FRAMEWORKS = {
    "react",
    "vue",
    "svelte",
    "angular",
    "solid",
    "node",
    "next",
    "nuxt",
    "astro",
    "preact",
    "ember",
}


def normalize_package(raw: str) -> str:
    """Turn a raw package identifier into a canonical, display-friendly label.

    e.g. ``@tanstack-start/react-router`` -> ``Tanstack Router``,
    ``@vue/cli-plugin-router`` -> ``Vue Router``, ``react`` -> ``React``.
    """
    raw = (raw or "").strip()
    if not raw:
        return raw

    scope: str | None = None
    name = raw
    if raw.startswith("@"):
        parts = raw.split("/", 1)
        if len(parts) == 2:
            scope = parts[0][1:]  # drop the leading "@"
            name = parts[1]

    words = [w for w in re.split(r"[-_./\s]", name) if w]
    words = [w for w in words if w.lower() not in NOISE_WORDS]

    label_words: list[str] = []
    if scope:
        label_words.append(scope)
        # A brand scope already implies the framework, so drop a leading
        # generic framework word from the name for a cleaner label.
        if words and words[0].lower() in GENERIC_FRAMEWORKS:
            words = words[1:]

    label_words.extend(words)

    if not label_words:
        return raw

    return " ".join(w[:1].upper() + w[1:] for w in label_words)


SUPPORTED_MANIFESTS = (
    "package.json",
    "requirements.txt",
    "pyproject.toml",
)


def parse_package_json(content: str) -> set[str]:
    data = json.loads(content)

    packages = set()

    for section in (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    ):
        packages.update(data.get(section, {}).keys())

    return packages


def parse_requirements(content: str) -> set[str]:
    packages = set()

    for line in content.splitlines():
        line = line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        package = (
            line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip()
        )

        if package:
            packages.add(package)

    return packages


def parse_pyproject(content: str) -> set[str]:
    packages = set()

    data = tomllib.loads(content)

    project = data.get("project", {})

    for dep in project.get("dependencies", []):
        package = dep.split(">=")[0].split("==")[0].strip()

        if package:
            packages.add(package)

    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})

    packages.update(poetry.keys())

    return packages


PARSERS = {
    "package.json": parse_package_json,
    "requirements.txt": parse_requirements,
    "pyproject.toml": parse_pyproject,
}


def extract_repository_packages(
    owner: str,
    repo: str,
) -> list[str]:

    packages = set()

    contents = get_repository_contents(
        owner,
        repo,
    )

    for item in contents:
        if item["name"] not in SUPPORTED_MANIFESTS:
            continue

        parser = PARSERS[item["name"]]

        try:
            text = download_file(
                owner,
                repo,
                item["path"],
            )

            packages.update(parser(text))

        except Exception:
            continue

    return sorted(packages)


def extract_developer_packages(
    repositories: list[dict],
) -> dict[str, int]:

    counter = Counter()

    for repo in repositories:
        full_name = repo["full_name"]

        owner, repo_name = full_name.split("/", 1)

        try:
            packages = extract_repository_packages(
                owner,
                repo_name,
            )

            counter.update(
                norm for norm in (normalize_package(p) for p in packages) if norm
            )

        except Exception:
            continue

    return dict(counter)
