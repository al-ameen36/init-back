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
