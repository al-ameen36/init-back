import sys
from gh_issues import get_issues


def select_issue_interactively(repo: str) -> dict:
    """Fetches open issues from the repo and prompts the user to select one."""
    print(f"Fetching open issues from {repo}...")
    issues = get_issues(repo, limit=10)

    if not issues:
        print("No open issues found.")
        sys.exit(0)

    # Display issues for selection
    print(f"\nFound {len(issues)} open issues:\n")
    for i, issue in enumerate(issues, 1):
        labels = f" [{', '.join(issue['labels'])}]" if issue["labels"] else ""
        print(f"  {i}. #{issue['number']}: {issue['title']}{labels}")

    print()
    choice = input(f"Select an issue to analyze (1-{len(issues)}): ").strip()
    try:
        idx = int(choice) - 1
        selected = issues[idx]
        if not (0 <= idx < len(issues)):
            raise ValueError
    except (ValueError, IndexError):
        print("Invalid selection.")
        sys.exit(1)

    return selected
