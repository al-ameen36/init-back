from graph_sitter import Codebase


def perform_search(codebase: Codebase, queries: list[str]) -> dict[str, list[str]]:
    """Searches the codebase for the given queries and returns aggregated results.

    Returns:
        A dictionary mapping file paths to a list of formatted match snippets.
    """
    file_matches = {}

    for query in queries:
        # First try an exact symbol lookup
        symbol = codebase.get_symbol(query, optional=True)
        if symbol is not None:
            snippet = f"Symbol: {symbol.name}\nContext:\n{symbol.source[:200]}"
            file_matches.setdefault(symbol.filepath, []).append(snippet)
            continue

        # Fall back to regex search across all files
        for file in codebase.files:
            results = file.search(query)
            if results:
                for result in results:
                    snippet = f"Line {result.start_point[0] + 1}:\n{result.source.strip()[:120]}"
                    file_matches.setdefault(file.filepath, []).append(snippet)

    return file_matches
