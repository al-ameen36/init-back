"""Memgraph implementation of :class:`graph_store.base.GraphStore`.

Uses the Apache-2.0 ``neo4j`` Python driver purely as a Bolt client (Memgraph
speaks the Bolt protocol and openCypher, not the neo4j server). The application
depends only on the :class:`graph_store.base.GraphStore` interface; this module
is the swappable backend.

Notable openCypher / Memgraph specifics handled here:

* The Memgraph database is named ``memgraph`` (not neo4j's ``neo4j`` default).
* Index/constraint DDL is run as implicit ``session.run`` statements (Memgraph
  forbids DDL inside managed/multi-statement transactions).
* Idempotent writes use ``MERGE`` keyed on a composite ``uid``. Relationship
  uniqueness constraints are unsupported by Memgraph, so relationships are
  merged on their ``uid`` property instead.
* Shortest path uses Memgraph's built-in BFS (``[*BFS]``), not ``shortestPath``.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Iterable, Sequence

from neo4j import GraphDatabase
from neo4j.exceptions import ClientError

from graph_store.base import GraphStore
from graph_store.models import (
    BASE_LABEL,
    Direction,
    GraphNode,
    GraphRelationship,
    GraphStoreError,
    NodeKind,
    RelationshipType,
    node_uid,
)

logger = logging.getLogger("graph_store.memgraph")

DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_DATABASE = "memgraph"
BATCH_SIZE = 2000

# Label + property indexes/constraints created once at initialization.
_SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT ON (n:CGNode) ASSERT n.uid IS UNIQUE",
    "CREATE INDEX ON :CGNode(uid)",
    "CREATE INDEX ON :CGNode(repositoryId)",
    "CREATE INDEX ON :CGNode(repositoryId, name)",
    "CREATE INDEX ON :CGNode(repositoryId, filePath)",
    "CREATE INDEX ON :CGNode",
]


def _is_already_exists_error(exc: Exception) -> bool:
    """True if the error is just 'index/constraint already exists'."""
    msg = str(exc).lower()
    code = getattr(exc, "code", "") or ""
    return (
        "already exist" in msg
        or "already_exists" in str(code).lower()
        or "constrealreadyexists" in str(code).lower()
        or "indexalreadyexists" in str(code).lower()
    )


class MemgraphGraphStore(GraphStore):
    """Persistent code-graph store backed by a Memgraph instance."""

    def __init__(
        self,
        uri: str = DEFAULT_URI,
        user: str = "",
        password: str = "",
        database: str = DEFAULT_DATABASE,
        driver: Any | None = None,
    ) -> None:
        self._uri = uri
        self._auth = (user, password) if (user or password) else ("", "")
        self._database = database
        self._driver = driver
        self._initialized = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        if self._driver is None:
            self._driver = GraphDatabase.driver(self._uri, auth=self._auth)
        self._driver.verify_connectivity()
        self.initialize()

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def _session(self):
        if self._driver is None:
            raise GraphStoreError("Not connected. Call connect() first.")
        return self._driver.session(database=self._database)

    def initialize(self) -> None:
        for stmt in _SCHEMA_STATEMENTS:
            try:
                with self._session() as s:
                    s.run(stmt)
            except ClientError as exc:  # already created on a prior run
                if not _is_already_exists_error(exc):
                    raise
            except Exception as exc:  # non-fatal schema issues shouldn't block startup
                logger.warning("Skipping schema statement %r: %s", stmt, exc)
        self._initialized = True

    # ------------------------------------------------------------------ #
    # Write helpers
    # ------------------------------------------------------------------ #
    def _run_write(self, cypher: str, params: dict[str, Any]) -> None:
        with self._session() as s:
            s.run(cypher, params).consume()

    def _run_transaction(self, statements: list[tuple[str, dict[str, Any]]]) -> None:
        """Run every (cypher, params) inside one transaction; rollback on error."""
        with self._session() as s:
            tx = s.begin_transaction()
            try:
                for cypher, params in statements:
                    tx.run(cypher, params)
                tx.commit()
            except Exception:
                tx.rollback()
                raise

    @staticmethod
    def _node_props(node: GraphNode) -> dict[str, Any]:
        props = node.to_properties()
        props["updatedAt"] = time.time()
        return props

    @staticmethod
    def _rel_props(rel: GraphRelationship) -> dict[str, Any]:
        props = rel.to_properties()
        props["updatedAt"] = time.time()
        return props

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
        return self.upsert_node(node)

    def get_repository(self, repository_id: str) -> GraphNode | None:
        records = self._run_read(
            f"MATCH (n:{BASE_LABEL} {{repositoryId: $repo, kind: 'Repository'}}) "
            "RETURN n LIMIT 1",
            {"repo": repository_id},
        )
        if not records:
            return None
        return _node_to_model(records[0]["n"])

    def repository_exists(self, repository_id: str) -> bool:
        return self.get_repository(repository_id) is not None

    def clear_repository(self, repository_id: str) -> None:
        """Remove everything except the Repository node, so re-index is seamless."""
        self._run_write(
            "MATCH (n:CGNode {repositoryId: $repo}) "
            "WHERE n.kind <> 'Repository' DETACH DELETE n",
            {"repo": repository_id},
        )

    def delete_repository(self, repository_id: str) -> None:
        self._run_write(
            "MATCH (n:CGNode {repositoryId: $repo}) DETACH DELETE n",
            {"repo": repository_id},
        )

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def upsert_node(self, node: GraphNode) -> GraphNode:
        self.upsert_nodes([node])
        return node

    def upsert_nodes(self, nodes: Sequence[GraphNode]) -> int:
        by_kind: dict[NodeKind, list[GraphNode]] = defaultdict(list)
        for n in nodes:
            by_kind[n.kind].append(n)

        count = 0
        for kind, group in by_kind.items():
            for i in range(0, len(group), BATCH_SIZE):
                chunk = group[i : i + BATCH_SIZE]
                rows = [{"uid": n.uid, "props": self._node_props(n)} for n in chunk]
                cypher = (
                    f"UNWIND $rows AS row "
                    f"MERGE (n:{BASE_LABEL} {{uid: row.uid}}) "
                    f"SET n = row.props "
                    f"SET n:{kind.value}"
                )
                self._run_write(cypher, {"rows": rows})
                count += len(chunk)
        return count

    def upsert_relationship(self, relationship: GraphRelationship) -> GraphRelationship:
        self.upsert_relationships([relationship])
        return relationship

    def upsert_relationships(self, relationships: Sequence[GraphRelationship]) -> int:
        by_type: dict[RelationshipType, list[GraphRelationship]] = defaultdict(list)
        for r in relationships:
            by_type[r.type].append(r)

        count = 0
        for rtype, group in by_type.items():
            for i in range(0, len(group), BATCH_SIZE):
                chunk = group[i : i + BATCH_SIZE]
                rows = [
                    {
                        "uid": r.uid,
                        "sourceUid": r.source_uid,
                        "targetUid": r.target_uid,
                        "repositoryId": r.repository_id,
                        "props": self._rel_props(r),
                    }
                    for r in chunk
                ]
                cypher = (
                    "UNWIND $rows AS row "
                    "MERGE (s:CGNode {uid: row.sourceUid}) "
                    "  SET s.repositoryId = row.repositoryId "
                    "MERGE (t:CGNode {uid: row.targetUid}) "
                    "  SET t.repositoryId = row.repositoryId "
                    f"MERGE (s)-[r:{rtype.value} {{uid: row.uid}}]->(t) "
                    "SET r = row.props"
                )
                self._run_write(cypher, {"rows": rows})
                count += len(chunk)
        return count

    def delete_node(self, repository_id: str, node_id: str) -> None:
        self._run_write(
            "MATCH (n:CGNode {uid: $uid}) DETACH DELETE n",
            {"uid": node_uid(repository_id, node_id)},
        )

    def delete_relationship(self, repository_id: str, relationship_id: str) -> None:
        self._run_write(
            "MATCH (s)-[r]->(t) WHERE r.uid = $ruid DELETE r",
            {"ruid": relationship_id},
        )

    def delete_nodes_in_file(self, repository_id: str, file_path: str) -> None:
        """Drop a file's subgraph (its declared/defined symbols and their edges)."""
        statements = [
            (
                "MATCH (f:File {repositoryId: $repo, filePath: $path})"
                "-[:DECLARES|DEFINES]->(s) DETACH DELETE s",
                {"repo": repository_id, "path": file_path},
            ),
            (
                "MATCH (f:File {repositoryId: $repo, filePath: $path}) DETACH DELETE f",
                {"repo": repository_id, "path": file_path},
            ),
        ]
        self._run_transaction(statements)

    def index_repository(
        self,
        repository_id: str,
        nodes: Sequence[GraphNode],
        relationships: Sequence[GraphRelationship],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Atomically (re)build a repository graph in a single transaction."""
        repo_node = GraphNode(
            id=repository_id,
            repository_id=repository_id,
            language="",
            name=repository_id,
            kind=NodeKind.REPOSITORY,
            metadata=metadata or {},
        )
        statements: list[tuple[str, dict[str, Any]]] = [
            (
                "MATCH (n:CGNode {repositoryId: $repo}) "
                "WHERE n.kind <> 'Repository' DETACH DELETE n",
                {"repo": repository_id},
            ),
            (
                f"MERGE (n:{BASE_LABEL} {{uid: $uid}}) SET n = $props SET n:{repo_node.kind.value}",
                {"uid": repo_node.uid, "props": self._node_props(repo_node)},
            ),
        ]

        by_kind: dict[NodeKind, list[GraphNode]] = defaultdict(list)
        for n in nodes:
            by_kind[n.kind].append(n)
        for kind, group in by_kind.items():
            for i in range(0, len(group), BATCH_SIZE):
                chunk = group[i : i + BATCH_SIZE]
                rows = [{"uid": n.uid, "props": self._node_props(n)} for n in chunk]
                statements.append(
                    (
                        f"UNWIND $rows AS row "
                        f"MERGE (n:{BASE_LABEL} {{uid: row.uid}}) "
                        f"SET n = row.props "
                        f"SET n:{kind.value}",
                        {"rows": rows},
                    )
                )

        by_type: dict[RelationshipType, list[GraphRelationship]] = defaultdict(list)
        for r in relationships:
            by_type[r.type].append(r)
        for rtype, group in by_type.items():
            for i in range(0, len(group), BATCH_SIZE):
                chunk = group[i : i + BATCH_SIZE]
                rows = [
                    {
                        "uid": r.uid,
                        "sourceUid": r.source_uid,
                        "targetUid": r.target_uid,
                        "repositoryId": r.repository_id,
                        "props": self._rel_props(r),
                    }
                    for r in chunk
                ]
                statements.append(
                    (
                        "UNWIND $rows AS row "
                        "MERGE (s:CGNode {uid: row.sourceUid}) "
                        "  SET s.repositoryId = row.repositoryId "
                        "MERGE (t:CGNode {uid: row.targetUid}) "
                        "  SET t.repositoryId = row.repositoryId "
                        f"MERGE (s)-[r:{rtype.value} {{uid: row.uid}}]->(t) "
                        "SET r = row.props",
                        {"rows": rows},
                    )
                )

        self._run_transaction(statements)

    # ------------------------------------------------------------------ #
    # Raw query
    # ------------------------------------------------------------------ #
    def query(
        self, cypher: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        with self._session() as s:
            result = s.run(cypher, parameters or {})
            return [_serialize_record(dict(record)) for record in result]

    # ------------------------------------------------------------------ #
    # Primitives
    # ------------------------------------------------------------------ #
    def _run_read(self, cypher: str, params: dict[str, Any]) -> list:
        with self._session() as s:
            return list(s.run(cypher, params))

    def _get_node(self, uid: str) -> GraphNode | None:
        records = self._run_read(
            f"MATCH (n:{BASE_LABEL} {{uid: $uid}}) RETURN n", {"uid": uid}
        )
        if not records:
            return None
        return _node_to_model(records[0]["n"])

    def _find_nodes(
        self,
        repository_id: str,
        kind: NodeKind | None = None,
        **properties: Any,
    ) -> list[GraphNode]:
        clauses = ["n.repositoryId = $repositoryId"]
        params: dict[str, Any] = {"repositoryId": repository_id}
        if kind is not None:
            clauses.append("n.kind = $kind")
            params["kind"] = kind.value
        for key, value in properties.items():
            # Keys come only from internal callers (name, filePath, ...).
            clauses.append(f"n.{key} = ${key}")
            params[key] = value
        cypher = f"MATCH (n:{BASE_LABEL}) WHERE {' AND '.join(clauses)} RETURN n"
        return [_node_to_model(rec["n"]) for rec in self._run_read(cypher, params)]

    def _neighbor_pattern(
        self, direction: Direction, rel_types: Iterable[RelationshipType] | None
    ) -> str:
        if rel_types:
            types = "|".join(t.value for t in rel_types)
            rel = f":{types}"
        else:
            rel = ""
        if direction == Direction.OUT:
            return f"(start)-[r{rel}]->(other)"
        if direction == Direction.IN:
            return f"(other)-[r{rel}]->(start)"
        return f"(start)-[r{rel}]-(other)"

    def _neighbors(
        self,
        uid: str,
        direction: Direction,
        rel_types: Iterable[RelationshipType] | None,
    ) -> list[GraphNode]:
        pattern = self._neighbor_pattern(direction, rel_types)
        cypher = (
            f"MATCH (start:{BASE_LABEL} {{uid: $uid}}) "
            f"WITH start MATCH {pattern} "
            "WHERE other.repositoryId = start.repositoryId "
            "RETURN DISTINCT other"
        )
        return [
            _node_to_model(rec["other"]) for rec in self._run_read(cypher, {"uid": uid})
        ]

    def _expand(
        self,
        uid: str,
        hops: int,
        rel_types: Iterable[RelationshipType] | None,
    ) -> list[GraphNode]:
        types = [t.value for t in rel_types] if rel_types else []
        cypher = (
            f"MATCH (start:{BASE_LABEL} {{uid: $uid}}) WITH start "
            f"MATCH (start)-[rels*1..$hops]-(other:{BASE_LABEL}) "
            "WHERE other.repositoryId = start.repositoryId "
            "  AND ($types = [] OR ALL(rel IN rels WHERE type(rel) IN $types)) "
            "RETURN DISTINCT other"
        )
        return [
            _node_to_model(rec["other"])
            for rec in self._run_read(
                cypher, {"uid": uid, "hops": hops, "types": types}
            )
        ]

    def _shortest_path(
        self, source_uid: str, target_uid: str
    ) -> list[GraphNode] | None:
        cypher = (
            f"MATCH (a:{BASE_LABEL} {{uid: $uid_a}}), (b:{BASE_LABEL} {{uid: $uid_b}}) "
            "WHERE a.repositoryId = b.repositoryId "
            "WITH a, b "
            "MATCH p = (a)-[*BFS]-(b) "
            "RETURN p LIMIT 1"
        )
        records = self._run_read(cypher, {"uid_a": source_uid, "uid_b": target_uid})
        if not records:
            return None
        path = records[0]["p"]
        return [_node_to_model(n) for n in path.nodes]

    def _all_nodes(self, repository_id: str) -> list[GraphNode]:
        records = self._run_read(
            f"MATCH (n:{BASE_LABEL} {{repositoryId: $repo}}) RETURN n",
            {"repo": repository_id},
        )
        return [_node_to_model(rec["n"]) for rec in records]

    def _all_relationships(self, repository_id: str) -> list[GraphRelationship]:
        records = self._run_read(
            f"MATCH (s:{BASE_LABEL} {{repositoryId: $repo}})"
            f"-[r]->(t:{BASE_LABEL} {{repositoryId: $repo}}) RETURN r",
            {"repo": repository_id},
        )
        return [_rel_to_model(rec["r"]) for rec in records]


def _node_to_model(node) -> GraphNode:
    p = dict(node.items())
    return GraphNode(
        id=p["id"],
        repository_id=p["repositoryId"],
        language=p.get("language") or "",
        name=p.get("name") or "",
        kind=NodeKind(p["kind"]),
        file_path=p.get("filePath"),
        start_line=p.get("startLine"),
        end_line=p.get("endLine"),
        metadata=p.get("metadata") or {},
    )


def _rel_to_model(rel) -> GraphRelationship:
    p = dict(rel.items())
    return GraphRelationship(
        id=p["id"],
        repository_id=p["repositoryId"],
        type=RelationshipType(rel.type),
        source_id=p["sourceId"],
        target_id=p["targetId"],
        metadata=p.get("metadata") or {},
    )


def _serialize_record(record: dict[str, Any]) -> dict[str, Any]:
    return {k: _serialize_value(v) for k, v in record.items()}


def _serialize_value(value: Any) -> Any:
    from neo4j import Node, Path, Relationship

    if isinstance(value, Node):
        return dict(value.items())
    if isinstance(value, Relationship):
        out = dict(value.items())
        out["__type__"] = value.type
        return out
    if isinstance(value, Path):
        return {
            "nodes": [dict(n.items()) for n in value.nodes],
            "relationships": [
                {**dict(r.items()), "__type__": r.type} for r in value.relationships
            ],
        }
    return value
