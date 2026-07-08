from llm import analyze_issue
from graph_sitter import Codebase
from gh_issues import format_issue
from cli import select_issue_interactively
from search import perform_search

repo = "psf/requests"


def main():
    # 1. Fetch and select an issue interactively
    selected_issue = select_issue_interactively(repo)

    issue_text = format_issue(selected_issue)
    print(f"\n{'=' * 60}")
    print(f"Analyzing: #{selected_issue['number']} - {selected_issue['title']}")
    print(f"{'=' * 60}\n")

    # 2. Get search queries from LLM
    queries = analyze_issue(issue_text)

    # 3. Initialize codebase and search
    codebase = Codebase.from_repo(repo)
    perform_search(codebase, queries)


if __name__ == "__main__":
    main()
