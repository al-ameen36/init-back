from llm import analyze_issue, score_files
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
    print("Generating search queries...")
    queries = analyze_issue(issue_text)
    print(f"Queries: {queries}\n")

    # 3. Initialize codebase and search
    print("Searching codebase...")
    codebase = Codebase.from_repo(repo)
    file_matches = perform_search(codebase, queries)

    if not file_matches:
        print("No matches found in the codebase.")
        return

    # 4. Score files based on matches
    print(f"Found matches in {len(file_matches)} files. Scoring relevance...")
    scored_files = score_files(issue_text, file_matches)

    # 5. Sort by confidence score descending
    scored_files.sort(key=lambda x: x["confidence_score"], reverse=True)

    print("\n" + "=" * 80)
    print(f"{'CONFIDENCE':<12} | {'FILE PATH':<40} | REASONING")
    print("=" * 80)
    import textwrap

    for sf in scored_files:
        score_str = f"{sf['confidence_score']}%"
        reasoning = textwrap.shorten(sf["reasoning"], width=60, placeholder="...")
        print(f"{score_str:<12} | {sf['file']:<40} | {reasoning}")
    print("=" * 80)


if __name__ == "__main__":
    main()
