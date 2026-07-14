"""Abstract :class:`GraphStore` contract and reusable typed query helpers.

The store is deliberately parser-agnostic: it only knows about
:class:`graph_store.models.GraphNode` and :class:`graph_store.models.GraphRelationship`.
A repository parser hands the store fully-formed entities; it never receives
parser objects.

Concrete backends (Memgraph, in-memory test double) implement the small set of
*primitive* methods; the rich, domain-specific query API (find node by name,
get callers, expand neighbors, shortest path, ...) is implemented once here on
top of those primitives, so every backend exposes an identical, typed surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import Any

from graph_store.models import (
    Direction,
    GraphNode,
    GraphRelationship,
    GraphStoreError,
    NodeKind,
    RelationshipType,
    node_uid,
)


class GraphStore(ABC):
    """Persistence and query interface for parsed code graphs.

    Applications should depend on this interface, not on a concrete backend.
    """

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    @abstractmethod
    def connect(self) -> None:
        """Open the underlying connection (idempotent)."""

    @abstractmethod
    def close(self) -> None:
        """Close the underlying connection and release resources."""

    @abstractmethod
    def initialize(self) -> None:
        """Create indexes/constraints required by the store. Safe to call repeatedly."""

    # ------------------------------------------------------------------ #
    # Repository lifecycle
    # ------------------------------------------------------------------ #
    @abstractmethod
    def create_repository(
        self, repository_id: str, metadata: dict[str, Any] | None = None
    ) -> GraphNode:
        """Upsert the Repository node. Idempotent."""

    @abstractmethod
    def clear_repository(self, repository_id: str) -> None:
        """Remove all nodes/relationships for a repo *except* its Repository node.

        Intended before an incremental or full re-index: the repository metadata
        survives so callers do not have to recreate it.
        """

    @abstractmethod
    def delete_repository(self, repository_id: str) -> None:
        """Remove the entire graph for a repository, including the Repository node."""

    # ------------------------------------------------------------------ #
    # Writes (idempotent via MERGE)
    # ------------------------------------------------------------------ #
    @abstractmethod
    def upsert_node(self, node: GraphNode) -> GraphNode:
        """Insert or update a single node. Idempotent on ``node.uid``."""

    @abstractmethod
    def upsert_nodes(self, nodes: Sequence[GraphNode]) -> int:
        """Batch-insert/update nodes efficiently. Returns the number processed."""

    @abstractmethod
    def upsert_relationship(self, relationship: GraphRelationship) -> GraphRelationship:
        """Insert or update a single relationship. Idempotent on ``relationship.uid``."""

    @abstractmethod
    def upsert_relationships(self, relationships: Sequence[GraphRelationship]) -> int:
        """Batch-insert/update relationships efficiently. Returns number processed."""

    @abstractmethod
    def delete_node(self, repository_id: str, node_id: str) -> None:
        """Delete a node and all relationships attached to it."""

    @abstractmethod
    def delete_relationship(self, repository_id: str, relationship_id: str) -> None:
        """Delete a single relationship by its id."""

    @abstractmethod
    def delete_nodes_in_file(self, repository_id: str, file_path: str) -> None:
        """Remove a file's node, every symbol it declares/defines, and their edges.

        This is the incremental-update primitive: when a file changes or is
        deleted, call this then re-upsert the file's subgraph.
        """

    def index_repository(
        self,
        repository_id: str,
        nodes: Sequence[GraphNode],
        relationships: Sequence[GraphRelationship],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Atomically (re)build a repository's graph.

        Default implementation clears then writes; backends should override to
        perform the whole operation inside a single transaction so a failure
        never leaves a half-written graph behind.
        """
        self.clear_repository(repository_id)
        self.create_repository(repository_id, metadata)
        self.upsert_nodes(nodes)
        self.upsert_relationships(relationships)

    # ------------------------------------------------------------------ #
    # Raw query (backend-specific; in-memory raises NotImplementedError)
    # ------------------------------------------------------------------ #
    @abstractmethod
    def query(
        self, cypher: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Run a raw Cypher query and return a list of record dicts.

        Backends serialize nodes/relationships/paths into plain dicts. The
        typed helpers below should be preferred; this exists for advanced,
        backend-native queries.
        """

    # ------------------------------------------------------------------ #
    # Primitives (backend-specific)
    # ------------------------------------------------------------------ #
    @abstractmethod
    def _get_node(self, uid: str) -> GraphNode | None:
        """Fetch a single node by composite uid."""

    @abstractmethod
    def _find_nodes(
        self,
        repository_id: str,
        kind: NodeKind | None = None,
        **properties: Any,
    ) -> list[GraphNode]:
        """Find nodes in a repo by kind and arbitrary equality properties."""

    @abstractmethod
    def _neighbors(
        self,
        uid: str,
        direction: Direction,
        rel_types: Iterable[RelationshipType] | None,
    ) -> list[GraphNode]:
        """Return neighbor nodes of ``uid`` along the given direction/types."""

    @abstractmethod
    def _expand(
        self,
        uid: str,
        hops: int,
        rel_types: Iterable[RelationshipType] | None,
    ) -> list[GraphNode]:
        """Return all nodes reachable from ``uid`` within ``hops`` (exclusive of start)."""

    @abstractmethod
    def _shortest_path(
        self, source_uid: str, target_uid: str
    ) -> list[GraphNode] | None:
        """Return the node sequence (incl. endpoints) of the shortest path, or None."""

    @abstractmethod
    def _all_nodes(self, repository_id: str) -> list[GraphNode]:
        """All nodes belonging to a repository."""

    @abstractmethod
    def _all_relationships(self, repository_id: str) -> list[GraphRelationship]:
        """All relationships whose endpoints belong to a repository."""

    # ------------------------------------------------------------------ #
    # Typed query API (implemented once, on top of primitives)
    # ------------------------------------------------------------------ #
    def find_node_by_id(self, repository_id: str, node_id: str) -> GraphNode | None:
        return self._get_node(node_uid(repository_id, node_id))

    def find_node_by_name(
        self,
        repository_id: str,
        name: str,
        kind: NodeKind | None = None,
    ) -> list[GraphNode]:
        return self._find_nodes(repository_id, kind=kind, name=name)

    def find_file(self, repository_id: str, file_path: str) -> GraphNode | None:
        nodes = self._find_nodes(repository_id, kind=NodeKind.FILE, filePath=file_path)
        if not nodes:
            return None
        if len(nodes) > 1:
            raise GraphStoreError(
                f"Multiple File nodes for {file_path!r} in repo {repository_id!r}"
            )
        return nodes[0]

    def find_class(self, repository_id: str, name: str) -> list[GraphNode]:
        return self._find_nodes(repository_id, kind=NodeKind.CLASS, name=name)

    def find_function(self, repository_id: str, name: str) -> list[GraphNode]:
        return self._find_nodes(repository_id, kind=NodeKind.FUNCTION, name=name)

    def get_children(self, repository_id: str, node_id: str) -> list[GraphNode]:
        return self._neighbors(
            node_uid(repository_id, node_id),
            Direction.OUT,
            (RelationshipType.CONTAINS, RelationshipType.DECLARES),
        )

    def get_parent(self, repository_id: str, node_id: str) -> list[GraphNode]:
        return self._neighbors(
            node_uid(repository_id, node_id),
            Direction.IN,
            (RelationshipType.CONTAINS, RelationshipType.DECLARES),
        )

    def get_imports(self, repository_id: str, node_id: str) -> list[GraphNode]:
        return self._neighbors(
            node_uid(repository_id, node_id), Direction.OUT, (RelationshipType.IMPORTS,)
        )

    def get_imported_by(self, repository_id: str, node_id: str) -> list[GraphNode]:
        return self._neighbors(
            node_uid(repository_id, node_id), Direction.IN, (RelationshipType.IMPORTS,)
        )

    def get_callers(self, repository_id: str, node_id: str) -> list[GraphNode]:
        return self._neighbors(
            node_uid(repository_id, node_id), Direction.IN, (RelationshipType.CALLS,)
        )

    def get_callees(self, repository_id: str, node_id: str) -> list[GraphNode]:
        return self._neighbors(
            node_uid(repository_id, node_id), Direction.OUT, (RelationshipType.CALLS,)
        )

    def get_references(self, repository_id: str, node_id: str) -> list[GraphNode]:
        return self._neighbors(
            node_uid(repository_id, node_id),
            Direction.OUT,
            (RelationshipType.REFERENCES,),
        )

    def get_referenced_by(self, repository_id: str, node_id: str) -> list[GraphNode]:
        return self._neighbors(
            node_uid(repository_id, node_id),
            Direction.IN,
            (RelationshipType.REFERENCES,),
        )

    def get_definitions(self, repository_id: str, node_id: str) -> list[GraphNode]:
        return self._neighbors(
            node_uid(repository_id, node_id), Direction.OUT, (RelationshipType.DEFINES,)
        )

    def get_tests_for_symbol(self, repository_id: str, node_id: str) -> list[GraphNode]:
        return self._neighbors(
            node_uid(repository_id, node_id), Direction.IN, (RelationshipType.TESTS,)
        )

    def get_symbols_in_file(
        self, repository_id: str, file_path: str
    ) -> list[GraphNode]:
        file_node = self.find_file(repository_id, file_path)
        if file_node is None:
            return []
        return self._neighbors(
            file_node.uid,
            Direction.OUT,
            (RelationshipType.DECLARES, RelationshipType.DEFINES),
        )

    def get_files_in_directory(
        self, repository_id: str, directory_id: str
    ) -> list[GraphNode]:
        return [
            n
            for n in self._neighbors(
                node_uid(repository_id, directory_id),
                Direction.OUT,
                (RelationshipType.CONTAINS,),
            )
            if n.kind == NodeKind.FILE
        ]

    def get_repository_graph(
        self, repository_id: str
    ) -> tuple[list[GraphNode], list[GraphRelationship]]:
        return self._all_nodes(repository_id), self._all_relationships(repository_id)

    def expand_neighbors(
        self,
        repository_id: str,
        node_id: str,
        hops: int = 1,
        rel_types: Iterable[RelationshipType] | None = None,
    ) -> list[GraphNode]:
        if hops < 1:
            raise GraphStoreError("hops must be >= 1")
        return self._expand(node_uid(repository_id, node_id), hops, rel_types)

    def shortest_path(
        self,
        repository_id: str,
        source_id: str,
        target_id: str,
    ) -> list[GraphNode] | None:
        return self._shortest_path(
            node_uid(repository_id, source_id),
            node_uid(repository_id, target_id),
        )
