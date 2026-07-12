from openai import OpenAI
import json
import os

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY", "dummy"),
    base_url=os.environ.get("OPENAI_BASE_URL"),
)

# The configured model (e.g. google/gemma-4-31B-it served via vLLM) has a
# small context window (max_model_len=8192 tokens total). We must cap the input
# we send and always request a finite number of output tokens, otherwise the
# request fails with HTTP 400 ("maximum context length exceeded" / "0 output
# tokens"). 14000 chars ~= 3500 tokens, leaving room for the system prompt and
# ~2500 tokens of output.
MAX_INPUT_CHARS = 14000
MAX_OUTPUT_TOKENS = 2500


def _fit(text: str) -> str:
    """Truncates a prompt to fit within the model's input budget."""
    if len(text) <= MAX_INPUT_CHARS:
        return text
    return text[:MAX_INPUT_CHARS] + "\n...[truncated]"


def _extract_json(content: str):
    """Parses a JSON response, tolerating markdown fences and stray prose.

    vLLM may ignore the `structured_outputs` hint and return fenced/verbose
    text, so we defensively extract the first JSON array/object.
    """
    if not content:
        return None
    text = content.strip()
    # Strip a leading ```json ... ``` fence if present.
    if text.startswith("```"):
        parts = text.split("```", 2)
        if len(parts) >= 2:
            text = parts[1]
            if text[:4].lower() == "json":
                text = text[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first [...] or {...} span.
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == opener:
                    depth += 1
                elif text[i] == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break
    return None


def _chat_json(system_prompt: str, user_content: str, schema: dict) -> object | None:
    """Calls the chat model and returns parsed JSON (or None on failure)."""
    response = client.chat.completions.create(
        model=os.environ["OPENAI_MODEL"],
        temperature=0,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _fit(user_content)},
        ],
        extra_body={"structured_outputs": {"json": schema}},
    )
    return _extract_json(response.choices[0].message.content or "")


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
    data = _chat_json(SYSTEM_PROMPT, issue_text, SCHEMA)
    if not isinstance(data, list):
        return []
    return [str(t) for t in data]


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

    # Cap the number of files/snippets we feed to the model so the prompt stays
    # within the model's small context window.
    matches_text = ""
    for filepath, snippets in list(file_matches.items())[:50]:
        matches_text += f"\n--- File: {filepath} ---\n"
        matches_text += "\n\n".join(snippets[:8])
        matches_text += "\n"

    user_content = f"ISSUE:\n{issue_text}\n\nMATCHES:\n{matches_text}"

    data = _chat_json(SCORE_PROMPT, user_content, SCORE_SCHEMA)
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


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
        "required_skills": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "difficulty",
        "summary",
        "relevant_files",
        "investigation_path",
        "required_skills",
    ],
}

GUIDE_PROMPT = """
You are an expert software engineer helping a developer solve a GitHub issue.
You have the issue description and a list of files that were matched by a codebase search, along with their relevance scores and snippets.

Produce a structured investigation guide with the following fields:
- "difficulty": Your estimate of how hard the issue is to resolve. One of "Low", "Medium", "High".
- "summary": A concise 2-4 sentence summary of the issue and what needs to change.
- "relevant_files": An ordered list of file paths most relevant to resolving the issue, most important first.
- "investigation_path": An ordered list of short, actionable steps (3-6 steps) a contributor should follow, starting with reproducing/understanding the problem and ending with verification (e.g. running tests).
- "required_skills": A concise list of the specific technologies, languages, frameworks, or skills a developer needs to know to solve this issue (e.g. "React", "GraphQL", "Docker", "PostgreSQL"). Use canonical, recognizable names, one per item.

Keep every field strictly technical and brief.
"""


def generate_investigation_guide(issue_text: str, scored_files: list[dict]) -> dict:
    if not scored_files:
        return {
            "difficulty": "Medium",
            "summary": "No relevant files found. Please check the issue description or expand the search.",
            "relevant_files": [],
            "investigation_path": [],
            "required_skills": [],
        }

    # Format the input
    context = f"ISSUE:\n{issue_text}\n\nSCORED FILES:\n"
    for sf in scored_files:
        if sf["confidence_score"] > 0:
            context += f"- {sf['file']} (Score: {sf['confidence_score']}%): {sf['reasoning']}\n"

    data = _chat_json(GUIDE_PROMPT, context, GUIDE_SCHEMA)
    if not isinstance(data, dict):
        return {
            "difficulty": "Medium",
            "summary": "No guide could be generated.",
            "relevant_files": [],
            "investigation_path": [],
            "required_skills": [],
        }

    return data
