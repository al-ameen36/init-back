"""Parser-agnostic code-graph persistence layer.

The application should depend on :class:`graph_store.base.GraphStore` (the
interface) and obtain concrete instances via :func:`get_graph_store` /

:func:`create_graph_store`. The default backend is Memgraph; an in-memory
implementation is provided for tests and local development.

Nothing in this package imports a code parser, so it can be exercised with
hand-built :class:`graph_store.models.GraphNode` /

:class:`graph_store.models.GraphRelationship` instances.
"""

from __future__ import annotations

import os

from graph_store.base import GraphStore
from graph_store.memory import InMemoryGraphStore
from graph_store.models import (
    BASE_LABEL,
    Direction,
    GraphNode,
    GraphRelationship,
    GraphStoreError,
    NodeKind,
    RelationshipType,
    node_uid,
    relationship_uid,
)

__all__ = [
    "GraphStore",
    "GraphNode",
    "GraphRelationship",
    "NodeKind",
    "RelationshipType",
    "Direction",
    "GraphStoreError",
    "BASE_LABEL",
    "node_uid",
    "relationship_uid",
    "InMemoryGraphStore",
    "MemgraphGraphStore",
    "create_graph_store",
    "get_graph_store",
]

# Lazily-imported singleton so importing this package never requires the
# `neo4j` driver / a running Memgraph (e.g. when only the in-memory store is used).
_graph_store: GraphStore | None = None


def create_graph_store(
    uri: str | None = None,
    user: str = "",
    password: str = "",
    database: str = "memgraph",
) -> GraphStore:
    """Return a Memgraph-backed store configured from the environment.

    Override ``uri`` (and credentials) to point at a separately hosted Memgraph
    instance (e.g. a dedicated container or managed service).
    """
    from graph_store.memgraph import MemgraphGraphStore

    resolved_uri = uri or os.getenv("MEMGRAPH_URI", "bolt://localhost:7687")
    resolved_user = user or os.getenv("MEMGRAPH_USER", "")
    resolved_password = password or os.getenv("MEMGRAPH_PASSWORD", "")
    return MemgraphGraphStore(
        uri=resolved_uri,
        user=resolved_user,
        password=resolved_password,
        database=database,
    )


def get_graph_store() -> GraphStore:
    """Return the process-wide graph store, creating (and connecting) it lazily."""
    global _graph_store
    if _graph_store is None:
        _graph_store = create_graph_store()
        _graph_store.connect()
    return _graph_store


def reset_graph_store() -> None:
    """Drop the cached singleton (mainly for tests)."""
    global _graph_store
    if _graph_store is not None:
        _graph_store.close()
    _graph_store = None
