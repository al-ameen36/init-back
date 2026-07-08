
from graph_sitter import Codebase


def get_codebase(repo: str) -> Codebase:
    codebase = Codebase.from_repo(repo)
    return codebase
    