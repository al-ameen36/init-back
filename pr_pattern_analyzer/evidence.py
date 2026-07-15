from __future__ import annotations

import re
from collections import Counter

from git import Repo

from .models import CommitMetadata, Evidence

TEST_PATH_RE = re.compile(r"(^|/)(tests?|spec|specs)(/|$)")
TEST_NAME_RE = re.compile(r"(^|_)test_|_test$|\.test\.", re.IGNORECASE)
DOC_SUFFIXES = {"md", "rst", "txt", "adoc", "mdx"}
DOC_NAMES = {
    "readme",
    "changelog",
    "changes",
    "contributing",
    "code_of_conduct",
    "authors",
}
README_RE = re.compile(r"(^|/|_)readme(\.|$)", re.IGNORECASE)
CHANGELOG_RE = re.compile(r"(^|/|_)(change(log|s)|history)(\.|$)", re.IGNORECASE)

HUNK_RE = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
MAX_FILE_CHARS = 6000
MAX_TOTAL_CONTENT_CHARS = 24000
CONTEXT_MARGIN = 6
MAX_TOTAL_CONTEXT_CHARS = 16000

EXT_LANGUAGE = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".cs": "C#",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".hpp": "C++",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala",
}

SYMBOL_RE = re.compile(
    r"^\+\s*"
    r"(?:"
    r"(?:async\s+)?def\s+(\w+)"  # python
    r"|class\s+(\w+)"  # many langs
    r"|(?:export\s+)?(?:async\s+)?function\s+(\w+)"  # js/ts
    r"|func\s+(?:\([^)]*\)\s*)?(\w+)"  # go
    r"|(?:pub\s+)?(?:fn|struct|trait|enum|impl)\s+(\w+)"  # rust
    r")"
)


class EvidenceCollector:
    """Reads a merged-PR commit without modifying the repository.

    Honors the read-only constraint by resolving commit objects and reading
    blobs via GitPython instead of running `git checkout`. The "merge commit"
    and "parent commit" are obtained as objects and never checked out.
    """

    def __init__(self, repo_path: str) -> None:
        self._repo = Repo(repo_path)

    def collect(self, commit_sha: str) -> Evidence:
        merge = self._repo.commit(commit_sha)
        parents = merge.parents
        parent = parents[0] if parents else None

        diff_items = merge.diff(parent, create_patch=True) if parent else []

        source, test, doc = [], [], []
        source_added, source_mod, test_added, test_mod, doc_added = [], [], [], [], []
        readme_changed = changelog_changed = False
        symbols: list[str] = []
        source_context: dict[str, str] = {}
        test_context: dict[str, str] = {}
        total_context = 0

        diff_parts: list[str] = []
        for item in diff_items:
            path = item.b_path or item.a_path or ""
            is_new = bool(item.new_file)
            is_deleted = bool(item.deleted_file)
            patch = (
                item.diff.decode("utf-8", errors="replace")
                if isinstance(item.diff, (bytes, bytearray))
                else (item.diff or "")
            )
            diff_parts.append(f"--- a/{item.a_path or ''}\n+++ b/{item.b_path or ''}")
            if patch:
                diff_parts.append(patch)

            if self._is_test(path):
                test.append(path)
                if is_new:
                    test_added.append(path)
                elif not is_deleted:
                    test_mod.append(path)
                if not is_deleted:
                    test_context[path] = self._context(
                        merge, path, patch, total_context
                    )
                    total_context += len(test_context[path])
            elif self._is_doc(path):
                doc.append(path)
                if is_new:
                    doc_added.append(path)
                if README_RE.search(path):
                    readme_changed = True
                if CHANGELOG_RE.search(path):
                    changelog_changed = True
            else:
                source.append(path)
                if is_new:
                    source_added.append(path)
                elif not is_deleted:
                    source_mod.append(path)
                symbols.extend(self._symbols(patch))
                if not is_deleted:
                    source_context[path] = self._context(
                        merge, path, patch, total_context
                    )
                    total_context += len(source_context[path])

        diff_text = "\n".join(diff_parts)

        return Evidence(
            repo_name=self._repo_name(),
            merge_commit_sha=merge.hexsha,
            parent_commit_sha=parent.hexsha if parent else None,
            metadata=self._metadata(merge, parents, diff_items),
            source_files=source,
            test_files=test,
            doc_files=doc,
            source_files_added=source_added,
            source_files_modified=source_mod,
            test_files_added=test_added,
            test_files_modified=test_mod,
            doc_files_added=doc_added,
            readme_changed=readme_changed,
            changelog_changed=changelog_changed,
            tests_added=bool(test_added),
            tests_modified=bool(test_mod),
            symbols_affected=self._dedupe(symbols),
            repository_language=self._language(source),
            diff=diff_text,
            source_contents=self._load_contents(merge, source),
            test_contents=self._load_contents(merge, test),
            doc_contents=self._load_contents(merge, doc),
            source_context=source_context,
            test_context=test_context,
        )

    @staticmethod
    def _is_test(path: str) -> bool:
        return bool(TEST_PATH_RE.search(path) or TEST_NAME_RE.search(path))

    @staticmethod
    def _is_doc(path: str) -> bool:
        lowered = path.lower()
        if lowered.rsplit("/", 1)[-1].split(".", 1)[0] in DOC_NAMES:
            return True
        return lowered.rsplit(".", 1)[-1] in DOC_SUFFIXES or "/docs/" in lowered

    @staticmethod
    def _symbols(patch: str) -> list[str]:
        found: list[str] = []
        for line in patch.splitlines():
            m = SYMBOL_RE.match(line)
            if m:
                name = next(g for g in m.groups() if g)
                kind = (
                    "def"
                    if "def" in line or "func" in line or "fn" in line
                    else "class"
                )
                found.append(f"{kind}:{name}")
        return found

    @staticmethod
    def _context(commit, path: str, patch: str, used: int) -> str:
        if used >= MAX_TOTAL_CONTEXT_CHARS:
            return ""
        ranges = [(int(s), int(c)) for s, c in HUNK_RE.findall(patch)]
        blob = EvidenceCollector._safe_blob(commit, path)
        if blob is None:
            return ""
        lines = blob.decode("utf-8", errors="replace").splitlines()
        windows: list[str] = []
        for start, count in ranges:
            lo = max(1, start - CONTEXT_MARGIN)
            hi = start + count + CONTEXT_MARGIN
            windows.append("\n".join(lines[lo - 1 : hi]))
        text = "\n\n".join(windows)
        if len(text) > MAX_TOTAL_CONTEXT_CHARS - used:
            text = text[: MAX_TOTAL_CONTEXT_CHARS - used]
        return text

    @staticmethod
    def _language(source_files: list[str]) -> str:
        counts: Counter[str] = Counter()
        for path in source_files:
            ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
            lang = EXT_LANGUAGE.get(ext)
            if lang:
                counts[lang] += 1
        return counts.most_common(1)[0][0] if counts else "unknown"

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for it in items:
            if it not in seen:
                seen.add(it)
                out.append(it)
        return out

    @staticmethod
    def _safe_blob(commit, path: str):
        try:
            return commit.tree[path].data_stream.read()
        except Exception:
            return None

    def _load_contents(self, commit, paths: list[str]) -> dict[str, str]:
        contents: dict[str, str] = {}
        total = 0
        for path in paths:
            if total >= MAX_TOTAL_CONTENT_CHARS:
                break
            blob = self._safe_blob(commit, path)
            if blob is None:
                continue
            text = blob.decode("utf-8", errors="replace")
            if len(text) > MAX_FILE_CHARS:
                text = text[:MAX_FILE_CHARS] + "\n...[truncated]"
            contents[path] = text
            total += len(text)
        return contents

    def _metadata(self, merge, parents, diff_items) -> CommitMetadata:
        stats = merge.stats.files
        ins = sum(v.get("insertions", 0) for v in stats.values())
        dele = sum(v.get("deletions", 0) for v in stats.values())
        author = merge.author.name if merge.author else "unknown"
        when = merge.authored_datetime.isoformat() if merge.authored_datetime else ""
        return CommitMetadata(
            sha=merge.hexsha,
            short_sha=merge.hexsha[:7],
            message=(merge.message or "").strip().splitlines()[0],
            author=author,
            authored_at=when,
            is_merge=len(parents) > 1,
            parent_count=len(parents),
            files_changed=len(diff_items),
            insertions=ins,
            deletions=dele,
        )

    def _repo_name(self) -> str:
        try:
            url = str(self._repo.remotes[0].url).rstrip("/")
            return str(url.split("/")[-1])
        except Exception:
            return str(self._repo.working_dir or "unknown")
