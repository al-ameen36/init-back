from __future__ import annotations

import json
import logging
import os

from openai import OpenAI

logger = logging.getLogger("init.pr_pattern_analyzer")

# Input is truncated by characters; the OpenAI-compatible server enforces a
# hard token budget (input + max_tokens <= its max-model-len). Keep output well
# under that budget. The output cap is env-tunable (read per call in chat_json)
# so it can be adjusted without a rebuild. Default output is small: the analyzer
# only needs a compact JSON object, not a long generation.
MAX_INPUT_CHARS = int(os.environ.get("PR_PATTERN_MAX_INPUT_CHARS", "14000"))


def _client() -> OpenAI:
    return OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY", "dummy"),
        base_url=os.environ.get("OPENAI_BASE_URL"),
    )


def _fit(text: str) -> str:
    if len(text) <= MAX_INPUT_CHARS:
        return text
    return text[:MAX_INPUT_CHARS] + "\n...[truncated]"


def _extract_json(content: str):
    if not content:
        return None
    text = content.strip()
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


def chat_json(
    system_prompt: str,
    user_content: str,
    *,
    max_output_tokens: int | None = None,
    schema: dict | None = None,
) -> object | None:
    """Calls the chat model for structured JSON. Returns parsed object or None.

    When *schema* is provided it is passed to vLLM via the
    ``structured_outputs`` extra body, which strongly encourages JSON output
    conforming to the given JSON Schema.  The response is still extracted
    defensively with ``_extract_json`` in case the model wraps it in fences.

    *max_output_tokens* overrides the per-call default
    (``PR_PATTERN_MAX_OUTPUT_TOKENS``, 1024).
    """
    if max_output_tokens is None:
        max_output_tokens = int(os.environ.get("PR_PATTERN_MAX_OUTPUT_TOKENS", "1024"))
    logger.info(
        "LLM request model=%s base_url=%s input_chars=%d max_output_tokens=%d schema=%s",
        os.environ.get("OPENAI_MODEL"),
        os.environ.get("OPENAI_BASE_URL"),
        len(user_content),
        max_output_tokens,
        bool(schema),
    )
    try:
        kwargs: dict = {}
        if schema is not None:
            kwargs["extra_body"] = {"structured_outputs": {"json": schema}}
        response = _client().chat.completions.create(
            model=os.environ["OPENAI_MODEL"],
            temperature=0,
            max_tokens=max_output_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _fit(user_content)},
            ],
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM call failed: %s", exc)
        raise
    content = response.choices[0].message.content or ""
    logger.info("LLM response chars=%d", len(content))
    data = _extract_json(content)
    if data is None:
        logger.warning(
            "LLM returned no parseable JSON (model=%s). Raw content: %s",
            os.environ.get("OPENAI_MODEL"),
            content[:500],
        )
    return data
