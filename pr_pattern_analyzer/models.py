from __future__ import annotations

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Git-level models (used by EvidenceCollector)
# ---------------------------------------------------------------------------


class CommitMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sha: str
    short_sha: str
    message: str
    author: str
    authored_at: str
    is_merge: bool
    parent_count: int
    files_changed: int
    insertions: int
    deletions: int


class Evidence(BaseModel):
    """Structured, read-only snapshot of a merged PR used as agent input."""

    model_config = ConfigDict(extra="ignore")

    repo_name: str
    merge_commit_sha: str
    parent_commit_sha: str | None
    metadata: CommitMetadata

    source_files: list[str]
    test_files: list[str]
    doc_files: list[str]

    source_files_added: list[str]
    source_files_modified: list[str]
    test_files_added: list[str]
    test_files_modified: list[str]
    doc_files_added: list[str]

    readme_changed: bool
    changelog_changed: bool
    tests_added: bool
    tests_modified: bool

    symbols_affected: list[str]
    repository_language: str

    diff: str
    source_contents: dict[str, str]
    test_contents: dict[str, str]
    doc_contents: dict[str, str]
    source_context: dict[str, str]
    test_context: dict[str, str]


# ---------------------------------------------------------------------------
# Deterministic per-PR fact (no LLM)
# ---------------------------------------------------------------------------


class PRFact(BaseModel):
    """Compact, deterministic summary of one merged PR. Feeds the synthesis."""

    model_config = ConfigDict(extra="ignore")

    pr_number: int
    title: str
    body: str
    labels: list[str]
    sha: str

    files_changed: int
    insertions: int
    deletions: int

    source_files: list[str]
    test_files: list[str]
    doc_files: list[str]

    has_tests: bool
    has_docs: bool
    has_changelog: bool
    has_readme: bool

    scope: str  # "localized" | "moderate" | "broad"

    time_to_merge_hours: float
    review_rounds: int
    linked_issue: bool
    title_conventional: bool


# ---------------------------------------------------------------------------
# Synthesis output (LLM-generated)
# ---------------------------------------------------------------------------


class Recommendation(BaseModel):
    """A single evidence-backed recommendation for contributors."""

    model_config = ConfigDict(extra="ignore")

    title: str
    description: str
    priority: str  # "high" | "medium" | "low"
    evidence: list[str]


class ChecklistItem(BaseModel):
    """A pre-PR checklist item derived from merged-PR patterns."""

    model_config = ConfigDict(extra="ignore")

    text: str
    required: bool


class ExamplePR(BaseModel):
    """A merged PR that demonstrates good practices."""

    model_config = ConfigDict(extra="ignore")

    number: int
    title: str
    url: str
    summary: str


class ContributorPlaybook(BaseModel):
    """Structured output of the synthesis step. Storable and renderable."""

    model_config = ConfigDict(extra="ignore")

    summary: str
    recommendations: list[Recommendation]
    checklist: list[ChecklistItem]
    example_prs: list[ExamplePR]
    prs_analyzed: int
    repo: str
