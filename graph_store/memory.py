"""In-memory :class:`graph_store.base.GraphStore` used for tests and local dev.

Implements the same interface as :class:`graph_store.memgraph.MemgraphGraphStore`
using plain Python structures, so the full typed query API can be exercised
without a running Memgraph. Also handy as a dependency-injection double.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable, Sequence

from graph_store.base import GraphStore
from graph_store.models import (
    Direction,
    GraphNode,
    GraphRelationship,
    NodeKind,
    RelationshipType,
    node_uid,
)


def _snake_case(key: str) -> str:
    out: list[str] = []
    for ch in key:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


class InMemoryGraphStore(GraphStore):
    """A non-persistent graph store kept entirely in process memory."""

    def __init__(self) -> None:
        self._nodes: dict[str, GraphNode] = {}  # uid -> node
        self._rels: dict[str, GraphRelationship] = {}  # uid -> relationship
        # adjacency: uid -> [(rel_uid, direction)] for fast traversal
        self._adj: dict[str, list[tuple[str, Direction]]] = defaultdict(list)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        return None

    def close(self) -> None:
        return None

    def initialize(self) -> None:
        return None

    # ------------------------------------------------------------------ #
    # Repository lifecycle
    # ------------------------------------------------------------------ #
    def create_repository(
        self, repository_id: str, metadata: dict[str, Any] | None = None
    ) -> GraphNode:
        node = GraphNode(
            id=repository_id,
            repository_id=repository_id,
            language="",
            name=repository_id,
            kind=NodeKind.REPOSITORY,
            metadata=metadata or {},
        )
        self._nodes[node.uid] = node
        return node

    def get_repository(self, repository_id: str) -> GraphNode | None:
        repo_uid = node_uid(repository_id, repository_id)
        node = self._nodes.get(repo_uid)
        return node if node is not None and node.kind == NodeKind.REPOSITORY else None

    def repository_exists(self, repository_id: str) -> bool:
        return self.get_repository(repository_id) is not None

    def clear_repository(self, repository_id: str) -> None:
        for uid in [
            n.uid
            for n in self._nodes.values()
            if n.repository_id == repository_id and n.kind != NodeKind.REPOSITORY
        ]:
            self._remove_node(uid)

    def delete_repository(self, repository_id: str) -> None:
        for uid in [
            n.uid for n in self._nodes.values() if n.repository_id == repository_id
        ]:
            self._remove_node(uid)

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def upsert_node(self, node: GraphNode) -> GraphNode:
        self._nodes[node.uid] = node
        return node

    def upsert_nodes(self, nodes: Sequence[GraphNode]) -> int:
        for n in nodes:
            self._nodes[n.uid] = n
        return len(nodes)

    def upsert_relationship(self, relationship: GraphRelationship) -> GraphRelationship:
        self._rels[relationship.uid] = relationship
        self._index_rel(relationship)
        return relationship

    def upsert_relationships(self, relationships: Sequence[GraphRelationship]) -> int:
        for r in relationships:
            self._rels[r.uid] = r
            self._index_rel(r)
        return len(relationships)

    def _index_rel(self, rel: GraphRelationship) -> None:
        self._adj.setdefault(rel.source_uid, []).append((rel.uid, Direction.OUT))
        self._adj.setdefault(rel.target_uid, []).append((rel.uid, Direction.IN))

    def delete_node(self, repository_id: str, node_id: str) -> None:
        self._remove_node(node_uid(repository_id, node_id))

    def _remove_node(self, uid: str) -> None:
        self._nodes.pop(uid, None)
        rels = self._adj.pop(uid, [])
        for rel_uid, _ in rels:
            self._rels.pop(rel_uid, None)
        # Drop any relationship that pointed at the removed node.
        for other_uid, edges in list(self._adj.items()):
            kept = [(ru, d) for ru, d in edges if ru in self._rels]
            self._adj[other_uid] = kept

    def delete_relationship(self, repository_id: str, relationship_id: str) -> None:
        ruid = relationship_id
        rel = self._rels.pop(ruid, None)
        if rel is not None:
            self._adj[rel.source_uid] = [
                (ru, d) for ru, d in self._adj.get(rel.source_uid, []) if ru != ruid
            ]
            self._adj[rel.target_uid] = [
                (ru, d) for ru, d in self._adj.get(rel.target_uid, []) if ru != ruid
            ]

    def delete_nodes_in_file(self, repository_id: str, file_path: str) -> None:
        file_node = self.find_file(repository_id, file_path)
        if file_node is None:
            return
        symbol_uids = {
            s.uid for s in self.get_symbols_in_file(repository_id, file_path)
        }
        for uid in [file_node.uid, *symbol_uids]:
            self._remove_node(uid)

    def index_repository(
        self,
        repository_id: str,
        nodes: Sequence[GraphNode],
        relationships: Sequence[GraphRelationship],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.clear_repository(repository_id)
        self.create_repository(repository_id, metadata)
        self.upsert_nodes(nodes)
        self.upsert_relationships(relationships)

    # ------------------------------------------------------------------ #
    # Raw query (unsupported; typed helpers are preferred)
    # ------------------------------------------------------------------ #
    def query(self, cypher: str, parameters: dict[str, Any] | None = None):
        raise NotImplementedError("InMemoryGraphStore does not execute raw Cypher.")

    # ------------------------------------------------------------------ #
    # Primitives
    # ------------------------------------------------------------------ #
    def _get_node(self, uid: str) -> GraphNode | None:
        return self._nodes.get(uid)

    def _find_nodes(
        self,
        repository_id: str,
        kind: NodeKind | None = None,
        **properties: Any,
    ) -> list[GraphNode]:
        result = []
        for n in self._nodes.values():
            if n.repository_id != repository_id:
                continue
            if kind is not None and n.kind != kind:
                continue
            # Property keys use the Cypher (camelCase) names, e.g. ``filePath``;
            # the dataclass stores them as snake_case fields.
            if all(
                getattr(n, _snake_case(key), None) == value
                for key, value in properties.items()
            ):
                result.append(n)
        return result

    def _neighbors(
        self,
        uid: str,
        direction: Direction,
        rel_types: Iterable[RelationshipType] | None,
    ) -> list[GraphNode]:
        types = set(rel_types) if rel_types else None
        out = []
        for rel_uid, d in self._adj.get(uid, []):
            if d != direction and direction != Direction.BOTH:
                continue
            rel = self._rels.get(rel_uid)
            if rel is None:
                continue
            if types is not None and rel.type not in types:
                continue
            other_uid = rel.target_uid if d == Direction.OUT else rel.source_uid
            other = self._nodes.get(other_uid)
            if other is not None:
                out.append(other)
        return out

    def _expand(
        self,
        uid: str,
        hops: int,
        rel_types: Iterable[RelationshipType] | None,
    ) -> list[GraphNode]:
        types = set(rel_types) if rel_types else None
        visited: set[str] = {uid}
        frontier = [uid]
        for _ in range(hops):
            nxt = []
            for cur in frontier:
                for rel_uid, d in self._adj.get(cur, []):
                    rel = self._rels.get(rel_uid)
                    if rel is None:
                        continue
                    if types is not None and rel.type not in types:
                        continue
                    other_uid = rel.target_uid if d == Direction.OUT else rel.source_uid
                    if other_uid not in visited:
                        visited.add(other_uid)
                        nxt.append(other_uid)
            frontier = nxt
        return [self._nodes[u] for u in visited if u != uid and u in self._nodes]

    def _shortest_path(
        self, source_uid: str, target_uid: str
    ) -> list[GraphNode] | None:
        if source_uid not in self._nodes or target_uid not in self._nodes:
            return None
        prev: dict[str, str | None] = {source_uid: None}
        q: deque[str] = deque([source_uid])
        while q:
            cur = q.popleft()
            if cur == target_uid:
                break
            for rel_uid, d in self._adj.get(cur, []):
                rel = self._rels.get(rel_uid)
                if rel is None:
                    continue
                other = rel.target_uid if d == Direction.OUT else rel.source_uid
                if other not in prev:
                    prev[other] = cur
                    q.append(other)
        if target_uid not in prev:
            return None
        path = []
        cur: str | None = target_uid
        while cur is not None:
            path.append(self._nodes[cur])
            cur = prev[cur]
        path.reverse()
        return path

    def _all_nodes(self, repository_id: str) -> list[GraphNode]:
        return [n for n in self._nodes.values() if n.repository_id == repository_id]

    def _all_relationships(self, repository_id: str) -> list[GraphRelationship]:
        return [r for r in self._rels.values() if r.repository_id == repository_id]
