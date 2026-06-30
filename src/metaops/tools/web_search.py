import os
import logging
from typing import Literal, Optional, Union

from google.adk.tools import FunctionTool

logger = logging.getLogger(__name__)


def _client():
    try:
        from tavily import AsyncTavilyClient
    except ImportError:
        raise RuntimeError("tavily-python not installed. Run: pip install tavily-python")
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY not set in environment")
    return AsyncTavilyClient(api_key=api_key)


async def web_search(
    query: str,
    search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] = "basic",
    topic: Literal["general", "news", "finance"] = "general",
    max_results: int = 5,
    include_answer: Union[bool, Literal["basic", "advanced"]] = True,
    include_raw_content: Union[bool, Literal["markdown", "text"]] = False,
    include_images: bool = False,
    time_range: Optional[Literal["day", "week", "month", "year"]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    country: Optional[str] = None,
    include_domains: Optional[list[str]] = None,
    exclude_domains: Optional[list[str]] = None,
    auto_parameters: Optional[bool] = None,
    exact_match: Optional[bool] = None,
) -> dict:
    """Search the internet and return relevant results with optional AI-generated answer.

    Args:
        query: Search query string.
        search_depth: Speed/quality tradeoff — "ultra-fast" < "fast" < "basic" < "advanced".
        topic: Domain focus — "general" for web, "news" for recent news, "finance" for financial data.
        max_results: Number of results to return (1-20).
        include_answer: Include a Tavily-generated answer summary ("advanced" = deeper synthesis).
        include_raw_content: Include full page content ("markdown" or "text" format, or bool).
        include_images: Include image URLs from results.
        time_range: Filter results by recency — "day", "week", "month", or "year".
        start_date: Filter results after this date (ISO format: YYYY-MM-DD).
        end_date: Filter results before this date (ISO format: YYYY-MM-DD).
        country: Restrict results to a specific country (ISO 3166-1 alpha-2, e.g. "us", "fr").
        include_domains: Only return results from these domains (e.g. ["github.com"]).
        exclude_domains: Exclude results from these domains.
        auto_parameters: Let Tavily auto-select optimal search parameters for the query.
        exact_match: Treat query as exact phrase match.

    Returns:
        dict with keys: answer (str), results (list of {title, url, content, score}).
    """
    try:
        client = _client()
        kwargs = {k: v for k, v in {
            "search_depth": search_depth,
            "topic": topic,
            "max_results": max_results,
            "include_answer": include_answer,
            "include_raw_content": include_raw_content,
            "include_images": include_images,
            "time_range": time_range,
            "start_date": start_date,
            "end_date": end_date,
            "country": country,
            "include_domains": include_domains,
            "exclude_domains": exclude_domains,
            "auto_parameters": auto_parameters,
            "exact_match": exact_match,
        }.items() if v is not None}
        response = await client.search(query, **kwargs)
        return {
            "answer": response.get("answer", ""),
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "raw_content": r.get("raw_content", ""),
                    "score": round(r.get("score", 0.0), 3),
                    "published_date": r.get("published_date", ""),
                }
                for r in response.get("results", [])
            ],
        }
    except Exception as e:
        return {"error": str(e), "results": []}


async def web_extract(
    urls: Union[list[str], str],
    extract_depth: Literal["basic", "advanced"] = "basic",
    format: Literal["markdown", "text"] = "markdown",
    query: Optional[str] = None,
    chunks_per_source: Optional[int] = None,
    include_images: bool = False,
) -> dict:
    """Extract the full content of one or more web pages (up to 20 URLs).

    Use this when you already have URLs and need their full text content —
    not for discovery (use web_search for that).

    Args:
        urls: Single URL string or list of URLs to extract (max 20).
        extract_depth: "basic" = main content only; "advanced" = full page including hidden content.
        format: Output format — "markdown" (structured) or "text" (plain).
        query: Optional query to guide content extraction (chunks most relevant to this query).
        chunks_per_source: Number of content chunks to return per URL.
        include_images: Include image URLs found on the pages.

    Returns:
        dict with keys: results (list of {url, raw_content, images}), failed_results (list of {url, error}).
    """
    try:
        client = _client()
        kwargs = {k: v for k, v in {
            "extract_depth": extract_depth,
            "format": format,
            "query": query,
            "chunks_per_source": chunks_per_source,
            "include_images": include_images,
        }.items() if v is not None}
        response = await client.extract(urls, **kwargs)
        return {
            "results": response.get("results", []),
            "failed_results": response.get("failed_results", []),
        }
    except Exception as e:
        return {"error": str(e), "results": [], "failed_results": []}


async def web_crawl(
    url: str,
    max_depth: int = 2,
    max_breadth: int = 10,
    limit: int = 20,
    instructions: Optional[str] = None,
    select_paths: Optional[list[str]] = None,
    exclude_paths: Optional[list[str]] = None,
    select_domains: Optional[list[str]] = None,
    exclude_domains: Optional[list[str]] = None,
    allow_external: bool = False,
    extract_depth: Literal["basic", "advanced"] = "basic",
    format: Literal["markdown", "text"] = "markdown",
    chunks_per_source: Optional[int] = None,
) -> dict:
    """Crawl a website starting from a base URL and return content from multiple pages.

    Use for deep research on a single website — documentation, wikis, news sites, etc.

    Args:
        url: Starting URL to crawl from.
        max_depth: How many link-hops deep to follow (default 2).
        max_breadth: Maximum links to follow per page (default 10).
        limit: Maximum total pages to crawl (default 20).
        instructions: Natural language guidance on what pages to prioritize.
        select_paths: Only crawl URLs matching these path prefixes (e.g. ["/docs", "/blog"]).
        exclude_paths: Skip URLs matching these path prefixes.
        select_domains: Only follow links to these domains.
        exclude_domains: Skip links to these domains.
        allow_external: Follow links to external domains (default False).
        extract_depth: "basic" = main content; "advanced" = full page content.
        format: Output format — "markdown" or "text".
        chunks_per_source: Number of content chunks per page.

    Returns:
        dict with key: results (list of {url, raw_content}).
    """
    try:
        client = _client()
        kwargs = {k: v for k, v in {
            "max_depth": max_depth,
            "max_breadth": max_breadth,
            "limit": limit,
            "instructions": instructions,
            "select_paths": select_paths,
            "exclude_paths": exclude_paths,
            "select_domains": select_domains,
            "exclude_domains": exclude_domains,
            "allow_external": allow_external,
            "extract_depth": extract_depth,
            "format": format,
            "chunks_per_source": chunks_per_source,
        }.items() if v is not None}
        response = await client.crawl(url, **kwargs)
        return {"results": response.get("results", [])}
    except Exception as e:
        return {"error": str(e), "results": []}


async def web_map(
    url: str,
    max_depth: int = 2,
    max_breadth: int = 20,
    limit: int = 50,
    instructions: Optional[str] = None,
    select_paths: Optional[list[str]] = None,
    exclude_paths: Optional[list[str]] = None,
    allow_external: bool = False,
) -> dict:
    """Discover and return the URL structure of a website without extracting full content.

    Use this to understand a site's layout before deciding which pages to crawl or extract.

    Args:
        url: Starting URL to map from.
        max_depth: How many link-hops deep to follow (default 2).
        max_breadth: Maximum links to follow per page (default 20).
        limit: Maximum total URLs to discover (default 50).
        instructions: Natural language guidance on which sections to prioritize.
        select_paths: Only map URLs matching these path prefixes.
        exclude_paths: Skip URLs matching these path prefixes.
        allow_external: Follow links to external domains (default False).

    Returns:
        dict with key: results (list of {url}).
    """
    try:
        client = _client()
        kwargs = {k: v for k, v in {
            "max_depth": max_depth,
            "max_breadth": max_breadth,
            "limit": limit,
            "instructions": instructions,
            "select_paths": select_paths,
            "exclude_paths": exclude_paths,
            "allow_external": allow_external,
        }.items() if v is not None}
        response = await client.map(url, **kwargs)
        return {"results": response.get("results", [])}
    except Exception as e:
        return {"error": str(e), "results": []}


async def company_info(
    query: str,
    max_results: int = 5,
    search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] = "advanced",
    country: Optional[str] = None,
) -> dict:
    """Search for company information across news, finance, and general web sources simultaneously.

    Aggregates results from multiple topic searches for comprehensive company research.

    Args:
        query: Company name or description to research.
        max_results: Total number of top results to return across all topics.
        search_depth: Search quality — "advanced" recommended for company research.
        country: Restrict to a specific country (ISO 3166-1 alpha-2, e.g. "us").

    Returns:
        dict with key: results (list of {title, url, content, score, topic}).
    """
    try:
        client = _client()
        kwargs = {k: v for k, v in {
            "max_results": max_results,
            "search_depth": search_depth,
            "country": country,
        }.items() if v is not None}
        results = await client.get_company_info(query, **kwargs)
        return {"results": results}
    except Exception as e:
        return {"error": str(e), "results": []}


web_search_tool = FunctionTool(func=web_search)
web_extract_tool = FunctionTool(func=web_extract)
web_crawl_tool = FunctionTool(func=web_crawl)
web_map_tool = FunctionTool(func=web_map)
company_info_tool = FunctionTool(func=company_info)
