from graph_sitter import Codebase


def perform_search(codebase: Codebase, queries: list[str]) -> None:
    """Searches the codebase for the given queries and prints the results."""
    for query in queries:
        # First try an exact symbol lookup
        symbol = codebase.get_symbol(query, optional=True)
        if symbol is not None:
            print(f"Symbol match for '{query}':")
            print(f"  {symbol.name} in {symbol.filepath}")
            print(f"  {symbol.source[:200]}")
            print()
            continue

        # Fall back to regex search across all files
        print(f"Searching files for '{query}':")
        found = False
        for file in codebase.files:
            results = file.search(query)
            if results:
                found = True
                for result in results:
                    print(f"  {file.filepath}:{result.start_point[0] + 1}")
                    # Show surrounding context: the parent statement/function
                    print(f"    ...{result.source.strip()[:120]}...")
        if not found:
            print("  (no results)")
        print()
