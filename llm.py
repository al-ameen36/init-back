from openai import OpenAI
import json
import os

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY", "dummy"),
    base_url=os.environ["OPENAI_BASE_URL"],
)

SCHEMA = {
    "type": "array",
    "items": {
        "type": "string",
    },
}

SYSTEM_PROMPT = """
You are an expert software engineer.

Given a GitHub issue, extract the most useful search terms for locating
relevant code inside a repository's source files.

The search terms will be used as regex patterns to search through file contents
(not just symbol names), so they should match text that would literally appear
in the source code of the repository.

Return ONLY a JSON array of strings (valid regex patterns).

Rules:
- Focus on distinctive strings that would appear in the repository's own code,
  NOT in Python's standard library (e.g. don't return "FileNotFoundError" unless
  the repo defines or catches it).
- Prioritize unique error message fragments, string literals, and distinctive
  variable/attribute names that appear in the issue (e.g. "cert_file",
  "Could not find the TLS certificate").
- Include attribute accesses as they'd appear in code (e.g. "conn\\.cert_file").
- Include function/method names that are likely defined in the repo.
- Return specific patterns first, generic ones last.
- Keep patterns short and precise — avoid overly broad terms.
- Do not include explanations.
"""


def analyze_issue(issue_text: str) -> list[str]:
    response = client.chat.completions.create(
        model=os.environ["OPENAI_MODEL"],
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": issue_text,
            },
        ],
        extra_body={
            "structured_outputs": {
                "json": SCHEMA,
            }
        },
    )
    content = response.choices[0].message.content
    if not content:
        return []

    terms = json.loads(content)

    return terms


SCORE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "file": {"type": "string"},
            "confidence_score": {"type": "integer"},
            "reasoning": {"type": "string"},
        },
        "required": ["file", "confidence_score", "reasoning"],
    },
}

SCORE_PROMPT = """
You are an expert software engineer.

You will be given a GitHub issue and a list of files with contextual code snippets 
that matched search queries derived from the issue.

Your task is to evaluate how relevant each file is to resolving the issue.
Return a JSON array of objects. Each object must have:
- "file": The file path.
- "confidence_score": An integer from 0 to 100 indicating relevance.
- "reasoning": A very short explanation (max 10 words) of why the file is or isn't relevant based on the matches.

Focus heavily on the context of the snippets and how they relate to the core problem described in the issue.
"""


def score_files(issue_text: str, file_matches: dict[str, list[str]]) -> list[dict]:
    if not file_matches:
        return []

    # Format matches into a text block
    matches_text = ""
    for filepath, snippets in file_matches.items():
        matches_text += f"\n--- File: {filepath} ---\n"
        matches_text += "\n\n".join(snippets)
        matches_text += "\n"

    user_content = f"ISSUE:\n{issue_text}\n\nMATCHES:\n{matches_text}"

    response = client.chat.completions.create(
        model=os.environ["OPENAI_MODEL"],
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": SCORE_PROMPT,
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        extra_body={
            "structured_outputs": {
                "json": SCORE_SCHEMA,
            }
        },
    )

    content = response.choices[0].message.content
    if not content:
        return []

    return json.loads(content)


GUIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "difficulty": {
            "type": "string",
            "enum": ["Low", "Medium", "High"],
        },
        "summary": {"type": "string"},
        "relevant_files": {
            "type": "array",
            "items": {"type": "string"},
        },
        "investigation_path": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["difficulty", "summary", "relevant_files", "investigation_path"],
}

GUIDE_PROMPT = """
You are an expert software engineer helping a developer solve a GitHub issue.
You have the issue description and a list of files that were matched by a codebase search, along with their relevance scores and snippets.

Produce a structured investigation guide with the following fields:
- "difficulty": Your estimate of how hard the issue is to resolve. One of "Low", "Medium", "High".
- "summary": A concise 2-4 sentence summary of the issue and what needs to change.
- "relevant_files": An ordered list of file paths most relevant to resolving the issue, most important first.
- "investigation_path": An ordered list of short, actionable steps (3-6 steps) a contributor should follow, starting with reproducing/understanding the problem and ending with verification (e.g. running tests).

Keep every field strictly technical and brief.
"""


def generate_investigation_guide(issue_text: str, scored_files: list[dict]) -> dict:
    if not scored_files:
        return {
            "difficulty": "Medium",
            "summary": "No relevant files found. Please check the issue description or expand the search.",
            "relevant_files": [],
            "investigation_path": [],
        }

    # Format the input
    context = f"ISSUE:\n{issue_text}\n\nSCORED FILES:\n"
    for sf in scored_files:
        if sf["confidence_score"] > 0:
            context += f"- {sf['file']} (Score: {sf['confidence_score']}%): {sf['reasoning']}\n"

    response = client.chat.completions.create(
        model=os.environ["OPENAI_MODEL"],
        temperature=0,
        messages=[
            {"role": "system", "content": GUIDE_PROMPT},
            {"role": "user", "content": context},
        ],
        extra_body={
            "structured_outputs": {
                "json": GUIDE_SCHEMA,
            }
        },
    )

    content = response.choices[0].message.content
    if not content:
        return {
            "difficulty": "Medium",
            "summary": "No guide could be generated.",
            "relevant_files": [],
            "investigation_path": [],
        }

    return json.loads(content)
