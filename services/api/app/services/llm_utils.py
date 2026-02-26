import json
import re


def parse_json_response(raw: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences and preamble.

    Handles: bare JSON, ```json fences, text before/after JSON block.
    CANONICAL IMPLEMENTATION: Worker imports this via `from app.services.llm_utils
    import parse_json_response` (worker Dockerfile copies api/app to /app/app).
    """
    text = raw.strip()

    # Strategy 1: Strip markdown fences (```json ... ```)
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Strategy 2: Find first { ... last } in the string
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        text = text[first_brace : last_brace + 1]

    return json.loads(text)
