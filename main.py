from llm import analyze_issue
from graph_sitter import Codebase
from issue_text import ISSUE


repo = "psf/requests"


if __name__ == "__main__":
    codebase = Codebase.from_repo(repo)

    queries = analyze_issue(ISSUE)

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
                    print(f"  {file.filepath}:{result.start_point[0]+1}")
                    # Show surrounding context: the parent statement/function
                    print(f"    ...{result.source.strip()[:120]}...")
        if not found:
            print("  (no results)")
        print()
