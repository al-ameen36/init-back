from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter

import tomllib

logger = logging.getLogger(__name__)

from features.github import (
    download_file,
    get_repository_contents,
)

# Local wrapper around @specfy/stack-analyser (the maintained, +700 tech
# ruleset), installed in the image at /opt/analyser. We materialize a repo's
# manifests into a temp dir and invoke its Node script; when it is missing or
# fails we fall back to the in-house parsers + canonicalize below.
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

# Words that carry no tech meaning on their own and are dropped from labels.
NOISE_WORDS = {
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

# npm scopes that own a framework/ecosystem: collapse any
# ``@scope/*`` sub-package to that framework.
SCOPE_MAP = {
    "angular": "Angular",
    "vue": "Vue",
    "svelte": "Svelte",
    "sveltejs": "Svelte",
    "nuxt": "Nuxt",
    "nestjs": "NestJS",
    "tanstack": "Tanstack",
    "mui": "MUI",
    "chakra-ui": "Chakra UI",
    "emotion": "Emotion",
    "reduxjs": "Redux",
    "remix-run": "Remix",
    "solidjs": "Solid",
    "ionic-team": "Ionic",
    "vuetifyjs": "Vuetify",
    "radix-ui": "Radix UI",
    "headlessui": "Headless UI",
    "react-hook-form": "React Hook Form",
    "react-navigation": "React Navigation",
    "apollographql": "Apollo",
    "trpc": "tRPC",
    "payloadcms": "Payload",
    "refine": "Refine",
    "supabase": "Supabase",
    "pocketbase": "PocketBase",
    "prisma": "Prisma",
    "graphql": "GraphQL",
}

# Scopes whose packages are build/dev tooling, never a skill.
EXCLUDE_SCOPES = {
    "types",
    "babel",
    "vitejs",
    "eslint",
    "typescript",
    "rollup",
    "swc",
}

# Tokens that identify build/dev tooling / package managers rather than a
# developer "skill"; dropped from the displayed tech stack.
TOOLING_TOKENS = {
    # package managers / monorepo
    "npm",
    "yarn",
    "pnpm",
    "bun",
    "deno",
    "turbo",
    "nx",
    "lerna",
    "changesets",
    "semantic-release",
    "release-please",
    "tsup",
    "unbuild",
    # bundlers / transpilers
    "babel",
    "webpack",
    "esbuild",
    "swc",
    "rollup",
    "parcel",
    "vite",
    # linters / formatters / css tooling
    "eslint",
    "prettier",
    "postcss",
    "autoprefixer",
    "stylelint",
    "husky",
    "lint",
    # types / testing
    "typescript",
    "tsc",
    "tslib",
    "ts-node",
    "@types",
    "jest",
    "vitest",
    "mocha",
    "chai",
    "cypress",
    "playwright",
    "storybook",
    # misc utils
    "dotenv",
    "cross",
    "env",
    "nanoid",
    "uuid",
    "rimraf",
    "clsx",
    "classnames",
    "lodash",
}

# Token -> canonical tech, across ecosystems. Sub-package tokens
# (react-router, sqlalchemy, serde, gin, ...) collapse to the parent.
ECOSYSTEM = {
    # JS / TS
    "react": "React",
    "next": "Next.js",
    "nuxt": "Nuxt",
    "vue": "Vue",
    "svelte": "Svelte",
    "angular": "Angular",
    "solid": "Solid",
    "preact": "Preact",
    "ember": "Ember",
    "astro": "Astro",
    "express": "Express",
    "nest": "NestJS",
    "koa": "Koa",
    "fastify": "Fastify",
    "graphql": "GraphQL",
    "prisma": "Prisma",
    "redux": "Redux",
    "zustand": "Zustand",
    "recoil": "Recoil",
    "tailwindcss": "Tailwind CSS",
    "three": "Three.js",
    "d3": "D3",
    "axios": "Axios",
    "trpc": "tRPC",
    "apollo": "Apollo",
    # Python
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "sqlalchemy": "SQLAlchemy",
    "numpy": "NumPy",
    "pandas": "Pandas",
    "torch": "PyTorch",
    "tensorflow": "TensorFlow",
    "scipy": "SciPy",
    "matplotlib": "Matplotlib",
    "requests": "Requests",
    "pytest": "Pytest",
    "celery": "Celery",
    "pydantic": "Pydantic",
    "scrapy": "Scrapy",
    # Rust
    "serde": "Serde",
    "tokio": "Tokio",
    "axum": "Axum",
    "actix": "Actix",
    "diesel": "Diesel",
    "rocket": "Rocket",
    "clap": "Clap",
    "rayon": "Rayon",
    # Go
    "gin": "Gin",
    "fiber": "Fiber",
    "echo": "Echo",
    # Other
    "rails": "Rails",
    "laravel": "Laravel",
    "spring": "Spring",
    "dotnet": ".NET",
}

# Explicit overrides for frameworks the token fallback would mislabel.
CANONICAL = {
    "tanstack start": "Tanstack Start",
    "tanstack query": "React Query",
    "next js": "Next.js",
    "node js": "Node.js",
    "mui material": "MUI",
}


def canonicalize(raw: str) -> str | None:
    """Map a raw package identifier to a high-level tech, or ``None`` to
    drop it (build/dev tooling). Resolution order:
      1. drop tooling scopes / tokens,
      2. exact normalized-label override,
      3. brand scope -> framework (``@mui/*`` -> MUI),
      4. ecosystem token fallback (``react-router`` -> React),
      5. the cleaned label.
    """
    norm = normalize_package(raw)
    if not norm:
        return None

    low = (raw or "").lower()
    scope: str | None = None
    if low.startswith("@"):
        scope = low[1:].split("/", 1)[0]

    raw_tokens = {t for t in re.split(r"[-_./\s]", low) if t and t not in NOISE_WORDS}

    if scope in EXCLUDE_SCOPES or any(tok in TOOLING_TOKENS for tok in raw_tokens):
        return None
    if norm.lower() in CANONICAL:
        return CANONICAL[norm.lower()]
    if scope in SCOPE_MAP:
        return SCOPE_MAP[scope]
    for tok in raw_tokens:
        if tok in ECOSYSTEM:
            return ECOSYSTEM[tok]
    return norm


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
    "go.mod",
    "Cargo.toml",
    "composer.json",
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


def _extract_local(files: dict[str, str]) -> list[str]:
    """In-house fallback: parse manifests and canonicalize package names."""
    packages = set()

    for path, text in files.items():
        parser = PARSERS.get(os.path.basename(path))
        if parser is None:
            continue
        try:
            packages.update(parser(text))
        except Exception:
            continue

    out: list[str] = []
    for raw in packages:
        canon = canonicalize(raw)
        if canon is not None:
            out.append(canon)
    return out


def extract_repository_packages(
    owner: str,
    repo: str,
) -> list[str]:
    """Return the list of tech names used by a repository.

    Primary path uses the maintained stack-analyser ruleset via the bundled
    local analyser; if that is unavailable we fall back to the in-house
    parsers.
    """
    contents = get_repository_contents(owner, repo)

    files: dict[str, str] = {}
    for item in contents:
        if item["name"] not in SUPPORTED_MANIFESTS:
            continue
        try:
            files[item["path"]] = download_file(owner, repo, item["path"])
        except Exception:
            continue

    if not files:
        return []

    techs = _call_local_analyser(files)
    local = _extract_local(files)

    # Service unreachable: rely entirely on the in-house fallback.
    if techs is None:
        return local

    # Union the maintained ruleset with the local parser so we keep coverage
    # the sidecar under-detects (e.g. Rust/Go crate-level frameworks) without
    # double counting.
    sidecar_names = [
        info["name"] for info in techs.values() if info.get("type") in RELEVANT_TYPES
    ]
    merged: list[str] = []
    seen: set[str] = set()
    for name in sidecar_names + local:
        if name not in seen:
            seen.add(name)
            merged.append(name)
    return merged


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
