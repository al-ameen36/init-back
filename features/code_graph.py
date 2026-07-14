"""Graph-store backed code analysis.

This module is the bridge between the parser (``graph_sitter``) and the
persistent :class:`graph_store.base.GraphStore`. The expensive ``Codebase``
parse runs **once per repo**: after that the normalized graph lives in
Memgraph and every later request is served straight from the store, so the
parser is never re-invoked unless the repo's graph is missing.

The analysis path (``analyze_one``) is driven entirely by the ``GraphStore``
interface, never by a live ``Codebase`` object.
"""

from __future__ import annotations

import os
from typing import Any

from graph_sitter import Codebase

from graph_store import get_graph_store
from graph_store.base import GraphStore
from graph_store.models import (
    GraphNode,
    GraphRelationship,
    NodeKind,
    RelationshipType,
    node_uid,
    relationship_uid,
)

_EXT_LANG = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".rb": "ruby",
}


def _lang(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    return _EXT_LANG.get(ext, "unknown")


def _symbol_kind(sym: Any) -> NodeKind:
    t = type(sym).__name__
    if t == "Class":
        return NodeKind.CLASS
    if t == "Interface":
        return NodeKind.INTERFACE
    if t == "Function":
        parent = getattr(sym, "parent_symbol", None)
        if parent is not None and type(parent).__name__ == "Class":
            return NodeKind.METHOD
        return NodeKind.FUNCTION
    if t in ("Assignment", "GlobalVar", "GlobalVariable"):
        return NodeKind.VARIABLE
    if t == "Import":
        return NodeKind.IMPORT
    return NodeKind.FUNCTION


def _node_for(sym: Any, repository_id: str) -> GraphNode | None:
    name = getattr(sym, "name", None)
    file_path = getattr(sym, "filepath", None)
    if not name or not file_path:
        return None
    sp = getattr(sym, "start_point", None)
    ep = getattr(sym, "end_point", None)
    return GraphNode(
        id=node_uid(file_path, name),
        repository_id=repository_id,
        language=_lang(file_path),
        name=name,
        kind=_symbol_kind(sym),
        file_path=file_path,
        start_line=(sp[0] + 1) if sp else None,
        end_line=(ep[0] + 1) if ep else None,
        metadata={"symbol_type": type(sym).__name__},
    )


def build_graph_from_repo(repository_id: str, store: GraphStore) -> None:
    """Parse ``repository_id`` with graph-sitter and persist it into ``store``.

    Raises if the repo can't be cloned/parsed so callers can surface the error.
    """
    codebase = Codebase.from_repo(repository_id)

    nodes: list[GraphNode] = []
    rels: list[GraphRelationship] = []
    seen_nodes: set[str] = set()
    seen_rels: set[str] = set()
    # Symbol objects kept for the dependency (call-graph) pass, run after every
    # node is known so we can skip edges to symbols outside this repository.
    symbols_to_process: list[Any] = []

    def add_node(n: GraphNode) -> None:
        if n.id not in seen_nodes:
            seen_nodes.add(n.id)
            nodes.append(n)

    def add_rel(r: GraphRelationship) -> None:
        if r.id not in seen_rels:
            seen_rels.add(r.id)
            rels.append(r)

    # First pass: directories, files, and all symbols + containment edges.
    for file in codebase.files:
        file_path = getattr(file, "filepath", None)
        if not file_path:
            continue
        parts = file_path.split("/")
        # Directory chain (src, src/app ...).
        for depth in range(1, len(parts)):
            dir_path = "/".join(parts[:depth])
            dir_name = parts[depth - 1]
            dir_uid = node_uid(dir_path, dir_name)
            add_node(
                GraphNode(
                    id=dir_uid,
                    repository_id=repository_id,
                    language=_lang(dir_path),
                    name=dir_name,
                    kind=NodeKind.DIRECTORY,
                    file_path=dir_path,
                )
            )
            if depth > 1:
                parent_path = "/".join(parts[: depth - 1])
                add_rel(
                    GraphRelationship(
                        id=relationship_uid(
                            node_uid(parent_path, parts[depth - 2]),
                            RelationshipType.CONTAINS,
                            dir_uid,
                        ),
                        repository_id=repository_id,
                        type=RelationshipType.CONTAINS,
                        source_id=node_uid(parent_path, parts[depth - 2]),
                        target_id=dir_uid,
                    )
                )
        # File node.
        file_name = parts[-1]
        file_uid = node_uid(file_path, file_name)
        add_node(
            GraphNode(
                id=file_uid,
                repository_id=repository_id,
                language=_lang(file_path),
                name=file_name,
                kind=NodeKind.FILE,
                file_path=file_path,
            )
        )
        # Directory -> file.
        if len(parts) > 1:
            parent_path = "/".join(parts[:-1])
            add_rel(
                GraphRelationship(
                    id=relationship_uid(
                        node_uid(parent_path, parts[-2]),
                        RelationshipType.CONTAINS,
                        file_uid,
                    ),
                    repository_id=repository_id,
                    type=RelationshipType.CONTAINS,
                    source_id=node_uid(parent_path, parts[-2]),
                    target_id=file_uid,
                )
            )

        # Symbols declared in this file.
        try:
            symbols = file.symbols(nested=True)
        except Exception:  # noqa: BLE001 - tolerate parser quirks
            symbols = []
        for sym in symbols:
            node = _node_for(sym, repository_id)
            if node is None:
                continue
            add_node(node)
            add_rel(
                GraphRelationship(
                    id=relationship_uid(file_uid, RelationshipType.DECLARES, node.id),
                    repository_id=repository_id,
                    type=RelationshipType.DECLARES,
                    source_id=file_uid,
                    target_id=node.id,
                )
            )
            # Parent symbol (class -> method, etc.).
            parent = getattr(sym, "parent_symbol", None)
            if parent is not None and type(parent).__name__ not in (
                "File",
                "SourceFile",
                "Import",
                "Export",
            ):
                pname = getattr(parent, "name", None)
                pfile = getattr(parent, "filepath", None)
                if pname and pfile:
                    add_rel(
                        GraphRelationship(
                            id=relationship_uid(
                                node_uid(pfile, pname),
                                RelationshipType.DEFINES,
                                node.id,
                            ),
                            repository_id=repository_id,
                            type=RelationshipType.DEFINES,
                            source_id=node_uid(pfile, pname),
                            target_id=node.id,
                        )
                    )
            symbols_to_process.append(sym)

    # Second pass: CALLS edges from graph-sitter's usage graph (`dependencies`).
    # Restricted to in-repo targets so we never create dangling nodes.
    for sym in symbols_to_process:
        src_uid = node_uid(getattr(sym, "filepath", ""), getattr(sym, "name", ""))
        if src_uid not in seen_nodes:
            continue
        deps = getattr(sym, "dependencies", None)
        if not deps:
            continue
        try:
            deps = list(deps)
        except Exception:  # noqa: BLE001
            deps = []
        for dep in deps:
            dname = getattr(dep, "name", None)
            dfile = getattr(dep, "filepath", None)
            if not dname or not dfile:
                continue
            tgt_uid = node_uid(dfile, dname)
            if tgt_uid not in seen_nodes or tgt_uid == src_uid:
                continue
            add_rel(
                GraphRelationship(
                    id=relationship_uid(src_uid, RelationshipType.CALLS, tgt_uid),
                    repository_id=repository_id,
                    type=RelationshipType.CALLS,
                    source_id=src_uid,
                    target_id=tgt_uid,
                )
            )

    store.index_repository(repository_id, nodes, rels)


def ensure_graph(repository_id: str, store: GraphStore | None = None) -> GraphStore:
    """Return a ``GraphStore`` that already contains ``repository_id``'s graph.

    If the graph is missing it is parsed from source and persisted first, so
    callers never pay for the parse twice.
    """
    if store is None:
        store = get_graph_store()
    if store.get_repository(repository_id) is not None:
        return store
    build_graph_from_repo(repository_id, store)
    return store


def list_file_paths(store: GraphStore, repository_id: str) -> set[str]:
    """All file paths known for the repository (used for fuzzy path resolution)."""
    nodes, _ = store.get_repository_graph(repository_id)
    return {n.file_path for n in nodes if n.kind == NodeKind.FILE and n.file_path}


def graph_search(
    store: GraphStore, repository_id: str, queries: list[str]
) -> dict[str, list[str]]:
    """Graph-store equivalent of ``features.search.perform_search``.

    Returns a mapping of file path -> list of snippet strings, exactly like the
    original so the downstream scoring pipeline is unchanged.
    """
    file_matches: dict[str, list[str]] = {}
    nodes, _ = store.get_repository_graph(repository_id)

    for query in queries:
        q = (query or "").strip()
        if not q:
            continue
        ql = q.lower()

        # Exact symbol name first.
        syms = store.find_node_by_name(repository_id, q)
        if syms:
            for s in syms:
                if not s.file_path:
                    continue
                snippet = f"Symbol: {s.name} ({s.kind.value})\nFile: {s.file_path}"
                file_matches.setdefault(s.file_path, []).append(snippet)
            continue

        # Substring over symbol names and file paths.
        for n in nodes:
            if n.file_path is None:
                continue
            if ql in (n.name or "").lower() or ql in n.file_path.lower():
                snippet = (
                    f"Symbol: {n.name} ({n.kind.value})\n"
                    f"Context:\n{n.kind.value} at {n.file_path}"
                    + (f":{n.start_line}" if n.start_line else "")
                )
                file_matches.setdefault(n.file_path, []).append(snippet)

    return file_matches
