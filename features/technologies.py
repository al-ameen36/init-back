from __future__ import annotations

from collections import Counter

import json
import tomllib

from features.github import (
    download_file,
    get_repository_contents,
)


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

            counter.update(packages)

        except Exception:
            continue

    return dict(counter)
