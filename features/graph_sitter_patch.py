"""Resilience patches for graph_sitter (0.56.14).

graph_sitter's `Codebase.from_repo` hard-crashes the *entire* build on the first
unhandled exception while parsing, resolving, or computing dependencies for any
single file/node. Real-world repos (e.g. prettier, with its huge generated
union fixtures and ambient `declare namespace` blocks) trigger many such bugs
that are not fixed upstream (0.56.14 is the latest release and its `develop`
branch is byte-identical for the affected code).

Rather than chase each individual bug, these patches make the build
*unit-fault-tolerant*: every per-file / per-node operation is guarded so a
single bad unit is logged and skipped while the rest of the graph still builds.
Good units are completely unaffected; only failures are contained.

Fixes:
  1. UnionType/TupleType._get_types recursed into nested union/intersection
     types via `yield from`, exceeding Python's recursion limit on large
     generated unions. Rewritten as an iterative BFS.
  2. TSImport.from_export_statement used `next(child …)` with no default,
     raising StopIteration on wildcard `export *` without an identifier child.
  3. SourceFile.from_content re-raised every parse error, aborting the whole
     build. Wrapped to return None (skip) on any failure -- consistent with the
     library's own contract (it already returns None for minified/syntax-error
     files and `_process_diff_files` already guards `if new_file is not None`).
  4. Import.add_symbol_resolution_edge raised OSError ("File name too long")
     when an import source resolved to an entire file's contents. Guarded.
  5. Export.compute_export_dependencies / Inherits.compute_superclass_dependencies
     / Importable.recompute can raise on malformed units. Guarded so one unit's
     failure does not abort the whole dependency-computation stage.
  6. CodebaseContext.build_graph wrapped as a final safety net: any unexpected
     error still yields a (partial) usable Codebase instead of a hard crash.
"""

import logging
from collections import deque
from collections.abc import Generator
from typing import TYPE_CHECKING

from graph_sitter.codebase.codebase_context import CodebaseContext
from graph_sitter.core.expressions.defined_name import DefinedName
from graph_sitter.core.expressions.tuple_type import TupleType
from graph_sitter.core.expressions.union_type import UnionType
from graph_sitter.core.file import SourceFile
from graph_sitter.core.import_resolution import Import
from graph_sitter.core.interfaces.importable import Importable
from graph_sitter.core.symbol import Symbol
from graph_sitter.enums import ImportType
from graph_sitter.typescript.import_resolution import TSImport
from graph_sitter.typescript.namespace import TSNamespace
from graph_sitter.utils import find_first_ancestor

from tree_sitter import Node as TSNode

logger = logging.getLogger("graph_sitter_patch")


def _guard(name: str, default=None):
    """Returns a decorator that swallows per-unit exceptions, logging them."""

    def decorator(func):
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:  # noqa: BLE001 - tolerate any malformed unit
                logger.warning(
                    "Skipping %s on %s (graph_sitter error: %s: %s)",
                    name,
                    getattr(self, "filepath", self),
                    type(e).__name__,
                    e,
                )
                return default

        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator


# ===== [ Fix 1: iterative _get_types ] =====


def _iterative_get_types(self, node: TSNode) -> Generator:
    queue: deque[TSNode] = deque([node])
    while queue:
        current = queue.popleft()
        for child in current.named_children:
            type_cls = self.ctx.node_classes.type_map.get(child.type, None)
            if isinstance(type_cls, type) and issubclass(type_cls, self.__class__):
                queue.append(child)
            else:
                yield self._parse_type(child)


UnionType._get_types = _iterative_get_types
TupleType._get_types = _iterative_get_types


# ===== [ Fix 2: StopIteration on wildcard namespace_export ] =====


if TYPE_CHECKING:
    from graph_sitter.codebase.codebase_context import CodebaseContext as _CB
    from graph_sitter.core.node_id_factory import NodeId
    from graph_sitter.typescript.statements.import_statement import TSImportStatement


@classmethod
def _safe_from_export_statement(
    cls,
    source_node: TSNode,
    file_node_id: "NodeId",
    ctx: "CodebaseContext",
    parent: "TSImportStatement",
) -> list[TSImport]:
    """Constructs import objects defined from an export statement."""
    export_statement_node = find_first_ancestor(source_node, ["export_statement"])
    if export_statement_node is None:
        return []
    imports = []
    if export_clause := next(
        (
            child
            for child in export_statement_node.named_children
            if child.type == "export_clause"
        ),
        None,
    ):
        # === [ Named export import ] ===
        # e.g. export { default as subtract } from './subtract';
        for export_specifier in export_clause.named_children:
            name = export_specifier.child_by_field_name("name")
            alias = export_specifier.child_by_field_name("alias") or name
            name_text = (
                name.text.decode("utf-8")
                if name is not None and name.text is not None
                else None
            )
            if name_text == "default":
                import_type = ImportType.DEFAULT_EXPORT
            else:
                import_type = ImportType.NAMED_EXPORT
            imp = cls(
                ts_node=export_statement_node,
                file_node_id=file_node_id,
                ctx=ctx,
                parent=parent,
                module_node=source_node,
                name_node=name,
                alias_node=alias,
                import_type=import_type,
            )
            imports.append(imp)
    else:
        # ==== [ Wildcard export import ] ====
        # Note: re-exporting using wildcard syntax does NOT include the default export!
        if namespace_export := next(
            (
                child
                for child in export_statement_node.named_children
                if child.type == "namespace_export"
            ),
            None,
        ):
            # Aliased wildcard export (e.g. export * as myNamespace from './m';)
            alias = next(
                (
                    child
                    for child in namespace_export.named_children
                    if child.type == "identifier"
                ),
                namespace_export,
            )
            imp = cls(
                ts_node=export_statement_node,
                file_node_id=file_node_id,
                ctx=ctx,
                parent=parent,
                module_node=source_node,
                name_node=namespace_export,
                alias_node=alias,
                import_type=ImportType.WILDCARD,
            )
            imports.append(imp)
        else:
            # No alias wildcard export (e.g. export * from './m';)
            imp = cls(
                ts_node=export_statement_node,
                file_node_id=file_node_id,
                ctx=ctx,
                parent=parent,
                module_node=source_node,
                name_node=None,
                alias_node=None,
                import_type=ImportType.WILDCARD,
            )
            imports.append(imp)
    return imports


TSImport.from_export_statement = _safe_from_export_statement  # ty: ignore


# ===== [ Fix 3: tolerate unparseable files ] =====

_original_from_content = SourceFile.from_content


@classmethod
def _safe_from_content(cls, filepath, content, ctx, *args, **kwargs):
    try:
        return _original_from_content.__func__(
            cls, filepath, content, ctx, *args, **kwargs
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Skipping file %s (graph_sitter parse failed: %s: %s)",
            filepath,
            type(e).__name__,
            e,
        )
        return None


SourceFile.from_content = _safe_from_content  # ty: ignore


# ===== [ Fix 3b: tolerate body-less symbols (e.g. ambient / malformed namespaces) ] =====
# `Symbol.__init__` unconditionally calls `self.code_block.parse()` even when
# `_parse_code_block()` returned None (no `body` field on the tree-sitter node).
# This happens for ambient `declare namespace X;` and for malformed parses such
# as prettier's `commentInNamespaceDeclarationWithIdentifierPathName.ts`, where
# a dotted namespace name on its own line detaches the `statement_block` as a
# sibling. Without this guard the AttributeError aborts parsing of the *entire*
# file. A body-less symbol simply has no code block; downstream dependency
# computation is already guarded (Fix 5), so the symbol is kept with no body.


_original_symbol_init = Symbol.__init__

# Ensure code_block always exists as an instance attribute default, even if
# __init__ is bypassed (e.g. __new__, unpickling, factory methods).
setattr(Symbol, "code_block", None)  # type: ignore[attr-defined]


def _safe_symbol_init(
    self, ts_node, file_id, ctx, parent, name_node=None, name_node_type=DefinedName
):
    self.code_block = None
    super(Symbol, self).__init__(ts_node, file_id, ctx, parent)
    name_node = self._get_name_node(ts_node) if name_node is None else name_node
    self._name_node = self._parse_expression(name_node, default=name_node_type)
    from graph_sitter.core.interfaces.has_block import HasBlock

    if isinstance(self, HasBlock):
        self.code_block = self._parse_code_block()
    self.parse(ctx)
    if isinstance(self, HasBlock) and self.code_block is not None:
        self.code_block.parse()


Symbol.__init__ = _safe_symbol_init  # ty: ignore


# ===== [ Fix 3c: body-less namespaces have no code block to compute deps on ] =====
# `TSNamespace._compute_dependencies` unconditionally calls
# `self.code_block._compute_dependencies(...)`. With Fix 3b a body-less namespace
# legitimately has `code_block is None`, so this would raise and be logged as an
# ERROR during the dependency stage. Treat a missing code block as "no
# dependencies to compute" instead.


_original_ns_compute = TSNamespace._compute_dependencies


def _safe_ns_compute(self, usage_type=None, dest=None):
    if self.code_block is None:
        return None
    return _original_ns_compute(self, usage_type, dest)


TSNamespace._compute_dependencies = _safe_ns_compute


# ===== [ Fix 3d: body-less symbols crash descendant_symbols ] =====
# `Symbol.descendant_symbols` unconditionally accesses
# `self.code_block.descendant_symbols`. With Fix 3b a body-less symbol
# legitimately has `code_block is None`, raising AttributeError.


_original_descendant_symbols = Symbol.descendant_symbols.fget


def _safe_descendant_symbols(self):
    if self.code_block is None:
        return [self]
    return _original_descendant_symbols(self)


Symbol.descendant_symbols = property(_safe_descendant_symbols)  # ty: ignore


# ===== [ Fix 4: tolerate unresolvable import paths ] =====

Import.add_symbol_resolution_edge = _guard("add_symbol_resolution_edge")(
    Import.add_symbol_resolution_edge
)


# ===== [ Fix 5: tolerate per-unit dependency-computation failures ] =====

from graph_sitter.core.export import Export  # noqa: E402
from graph_sitter.core.interfaces.inherits import Inherits  # noqa: E402

Export.compute_export_dependencies = _guard("compute_export_dependencies")(
    Export.compute_export_dependencies
)
Inherits.compute_superclass_dependencies = _guard("compute_superclass_dependencies")(
    Inherits.compute_superclass_dependencies
)
Importable.recompute = _guard("recompute", default=[])(Importable.recompute)


# ===== [ Fix 6: top-level safety net ] =====

_original_build_graph = CodebaseContext.build_graph


def _safe_build_graph(self, repo_operator):
    try:
        return _original_build_graph(self, repo_operator)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "build_graph aborted mid-build (graph_sitter error: %s: %s). "
            "Returning partial codebase.",
            type(e).__name__,
            e,
        )
        return None


CodebaseContext.build_graph = _safe_build_graph
