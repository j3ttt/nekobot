"""Extract <memory_write> tags from LLM responses."""

import re

MEMORY_WRITE_RE = re.compile(r"<memory_write>\n(.*?)\n</memory_write>", re.DOTALL)


def extract_memory_writes(response: str) -> tuple[str, list[tuple[str, str, str]]]:
    """
    Parse <memory_write> blocks from a response.

    Returns:
        (cleaned_response, [(category, key, value), ...])
    """
    facts: list[tuple[str, str, str]] = []
    for match in MEMORY_WRITE_RE.finditer(response):
        for line in match.group(1).strip().split("\n"):
            line = line.strip().lstrip("- ")
            if ":" not in line:
                continue
            full_key, value = line.split(":", 1)
            if "." in full_key:
                category, key = full_key.split(".", 1)
            else:
                category, key = "active", full_key
            facts.append((category.strip(), key.strip(), value.strip()))

    cleaned = MEMORY_WRITE_RE.sub("", response).strip()
    return cleaned, facts
