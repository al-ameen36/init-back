"""Parser-agnostic domain models for the code-graph persistence layer.

These types are the contract between a repository parser (currently
graph-sitter, but that may change) and the :class:`graph_store.base.GraphStore`
implementation. Nothing here imports a parser, so the persistence layer can be
exercised with hand-built graphs in tests and swapped between backends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Every node and relationship carries a composite unique key formed from the
# repository id and the parser-provided id. The unit-separator escape is used so
# that even ids containing "::" (C++ scopes) or "/" (paths) cannot collide.
UID_SEP = "\x1f"
BASE_LABEL = "CGNode"


class NodeKind(str, Enum):
    """Structural/semantic kinds of nodes in a code graph."""

    REPOSITORY = "Repository"
    DIRECTORY = "Directory"
    FILE = "File"
    CLASS = "Class"
    FUNCTION = "Function"
    METHOD = "Method"
    INTERFACE = "Interface"
    ENUM = "Enum"
    VARIABLE = "Variable"
    IMPORT = "Import"
    TEST = "Test"

    @property
    def label(self) -> str:
        return self.value


class RelationshipType(str, Enum):
    """Edge types that connect code-graph nodes."""

    CONTAINS = "CONTAINS"
    DECLARES = "DECLARES"
    CALLS = "CALLS"
    IMPORTS = "IMPORTS"
    REFERENCES = "REFERENCES"
    INHERITS = "INHERITS"
    IMPLEMENTS = "IMPLEMENTS"
    DEFINES = "DEFINES"
    USES = "USES"
    TESTS = "TESTS"


class Direction(str, Enum):
    OUT = "OUT"
    IN = "IN"
    BOTH = "BOTH"


def node_uid(repository_id: str, node_id: str) -> str:
    return f"{repository_id}{UID_SEP}{node_id}"


def relationship_uid(repository_id: str, rel_id: str) -> str:
    return f"{repository_id}{UID_SEP}{rel_id}"


@dataclass
class GraphNode:
    """A single entity in a code graph, independent of any parser.

    ``id`` is unique within its repository (the parser supplies it, e.g.
    ``"src/foo.py::MyClass::method"``). ``uid`` is the globally-unique composite
    key persisted in the store and used for idempotent MERGE.
    """

    id: str
    repository_id: str
    language: str
    name: str
    kind: NodeKind
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return node_uid(self.repository_id, self.id)

    def to_properties(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "id": self.id,
            "repositoryId": self.repository_id,
            "language": self.language,
            "name": self.name,
            "kind": self.kind.value,
            "filePath": self.file_path,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "metadata": self.metadata or {},
        }


@dataclass
class GraphRelationship:
    """A directed edge between two code-graph nodes.

    Direction convention (used by the query helpers): the subject of the verb
    is the source. e.g. ``(caller)-[CALLS]->(callee)``,
    ``(importer)-[IMPORTS]->(imported)``, ``(test)-[TESTS]->(tested)``.
    """

    id: str
    repository_id: str
    type: RelationshipType
    source_id: str
    target_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return relationship_uid(self.repository_id, self.id)

    @property
    def source_uid(self) -> str:
        return node_uid(self.repository_id, self.source_id)

    @property
    def target_uid(self) -> str:
        return node_uid(self.repository_id, self.target_id)

    def to_properties(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "id": self.id,
            "repositoryId": self.repository_id,
            "sourceId": self.source_id,
            "targetId": self.target_id,
            "metadata": self.metadata or {},
        }


class GraphStoreError(Exception):
    """Base class for all graph-store failures."""


class RepositoryNotFoundError(GraphStoreError):
    """Raised when an operation targets a repository that has no graph yet."""
