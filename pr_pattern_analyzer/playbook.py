from __future__ import annotations

import logging
from pathlib import Path

from .llm import chat_json
from .models import ContributorPlaybook, PRFact

logger = logging.getLogger("init.pr_pattern_analyzer")

PROMPT_DIR = Path(__file__).parent / "prompts"


def _format_facts(facts: list[PRFact]) -> str:
    """Serialize PRFacts into a compact, human-readable block for the LLM."""
    parts: list[str] = []
    for f in facts:
        body_preview = f.body[:300].replace("\n", " ") if f.body else "(empty)"
        parts.append(
            f"PR #{f.pr_number}: {f.title}\n"
            f"  labels: {', '.join(f.labels) if f.labels else '(none)'}\n"
            f"  sha: {f.sha}\n"
            f"  files: {f.files_changed} (+{f.insertions} -{f.deletions})\n"
            f"  scope: {f.scope}\n"
            f"  tests: {'yes' if f.has_tests else 'no'}"
            f"  docs: {'yes' if f.has_docs else 'no'}"
            f"  changelog: {'yes' if f.has_changelog else 'no'}"
            f"  readme: {'yes' if f.has_readme else 'no'}\n"
            f"  time_to_merge: {f.time_to_merge_hours:.1f}h\n"
            f"  review_rounds: {f.review_rounds}\n"
            f"  linked_issue: {'yes' if f.linked_issue else 'no'}\n"
            f"  title_conventional: {'yes' if f.title_conventional else 'no'}\n"
            f"  body: {body_preview}"
        )
    return "\n\n".join(parts)


def _playbook_schema() -> dict:
    """Return a JSON Schema dict for ContributorPlaybook (for vLLM structured outputs)."""
    schema = ContributorPlaybook.model_json_schema()
    # vLLM structured_outputs expects a top-level JSON Schema object.
    # Pydantic generates $defs for nested models — inline them so vLLM gets a
    # flat, self-contained schema.
    defs = schema.pop("$defs", {})
    if defs:
        _inline_defs(schema, defs)
    return schema


def _inline_defs(node: dict, defs: dict) -> None:
    """Recursively replace ``$ref`` pointers with their resolved definitions."""
    if isinstance(node, dict):
        ref = node.pop("$ref", None)
        if ref is not None:
            name = ref.rsplit("/", 1)[-1]
            if name in defs:
                node.update(defs[name])
                _inline_defs(node, defs)
            return
        for v in node.values():
            if isinstance(v, (dict, list)):
                _inline_defs(v, defs)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                _inline_defs(item, defs)


def build_playbook(facts: list[PRFact], repo: str) -> ContributorPlaybook:
    """Single LLM call that synthesizes a ContributorPlaybook from PRFacts."""
    system_prompt = (PROMPT_DIR / "synthesis.txt").read_text(encoding="utf-8")
    user_content = (
        f"REPOSITORY: {repo}\n\nMERGED PRS ({len(facts)} total):\n\n"
        + _format_facts(facts)
    )

    data = chat_json(
        system_prompt, user_content, max_output_tokens=4096, schema=_playbook_schema()
    )

    if not isinstance(data, dict):
        logger.warning(
            "Playbook synthesis returned no usable JSON; falling back to empty playbook."
        )
        return ContributorPlaybook(
            summary="",
            recommendations=[],
            checklist=[],
            example_prs=[],
            prs_analyzed=len(facts),
            repo=repo,
        )

    # Unwrap single-key wrapper if present
    fields = set(ContributorPlaybook.model_fields)
    if not (fields & set(data)) and len(data) == 1:
        inner = next(iter(data.values()))
        if isinstance(inner, dict) and (fields & set(inner)):
            data = inner

    try:
        return ContributorPlaybook.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Playbook validation failed: %s | data=%s", exc, data)
        return ContributorPlaybook(
            summary="",
            recommendations=[],
            checklist=[],
            example_prs=[],
            prs_analyzed=len(facts),
            repo=repo,
        )
