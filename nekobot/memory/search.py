"""Keyword search over archive/ directory."""

from pathlib import Path


def search_archive(archive_path: Path, query: str, max_results: int = 5) -> list[dict]:
    """
    Simple keyword search over archive .md files.

    Returns list of {path, title, snippet} dicts, sorted by relevance (match count).
    """
    if not archive_path.exists():
        return []

    query_lower = query.lower()
    keywords = query_lower.split()
    results = []

    for md_file in archive_path.rglob("*.md"):
        text = md_file.read_text()
        text_lower = text.lower()

        # Score: count keyword occurrences
        score = sum(text_lower.count(kw) for kw in keywords)
        if score == 0:
            continue

        # Extract title from first line
        first_line = text.split("\n", 1)[0].lstrip("# ").strip()

        # Extract snippet around first match
        snippet = _extract_snippet(text, keywords)

        results.append({
            "path": str(md_file.relative_to(archive_path)),
            "title": first_line,
            "snippet": snippet,
            "score": score,
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]


def _extract_snippet(text: str, keywords: list[str], context_chars: int = 150) -> str:
    """Extract a snippet around the first keyword match."""
    text_lower = text.lower()
    for kw in keywords:
        idx = text_lower.find(kw)
        if idx >= 0:
            start = max(0, idx - context_chars // 2)
            end = min(len(text), idx + len(kw) + context_chars // 2)
            snippet = text[start:end].replace("\n", " ").strip()
            if start > 0:
                snippet = "..." + snippet
            if end < len(text):
                snippet = snippet + "..."
            return snippet
    return text[:context_chars].replace("\n", " ").strip()
