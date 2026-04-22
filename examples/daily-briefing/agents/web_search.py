"""Web search helper for daily-briefing agents.

Uses DuckDuckGo search (no API key required) to fetch real data.
"""

import logging

logger = logging.getLogger(__name__)


def search(query: str, max_results: int = 5) -> str:
    """Search the web and return results as formatted text."""
    try:
        from ddgs import DDGS

        results = list(DDGS().text(query, max_results=max_results))

        if results:
            parts = []
            for r in results:
                parts.append(f"**{r['title']}**\n{r['body']}\n")
            return "\n".join(parts)

        return f"No search results found for: {query}"

    except Exception as e:
        logger.warning(f"Web search failed for '{query}': {e}")
        return f"Web search unavailable: {e}"
