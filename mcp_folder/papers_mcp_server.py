"""
Papers MCP server.

Exposes MCP tools backed by local MCP helper functions for:
- Searching papers by topic
- Getting paper summaries
- Requesting OpenAlex API endpoints

Run locally:
    python papers_mcp_server.py
"""

import logging
import os

from fastmcp import FastMCP

from mcp_tool_decorator import ensure_log_table_exists, log_mcp_tool_call

from papers_mcp_helper_functions import PapersMCPHelperFunctions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("papers-mcp-server")

papers_helper = PapersMCPHelperFunctions(per_page=25)

mcp = FastMCP("papers-service")


@mcp.tool
@log_mcp_tool_call
def search_topic(topic: str, per_page: int = 10, min_citations: int = 0) -> dict:
    """
    Search papers by topic in OpenAlex.

    Args:
        topic: Topic text query, for example "graph neural networks"
        per_page: Number of results (1-25)
        min_citations: Filter results by minimum cited_by_count
    """
    return papers_helper.search_topic(topic=topic, per_page=per_page, min_citations=min_citations)


@mcp.tool
@log_mcp_tool_call
def get_paper_summary(openalex_id: str) -> dict:
    """
    Get a concise summary for a paper from OpenAlex.

    Args:
        openalex_id: OpenAlex work id or URL, for example "W2741809807"
    """
    return papers_helper.get_paper_summary(openalex_id=openalex_id)


@mcp.tool
@log_mcp_tool_call
def request_openalex_api(endpoint: str, params: dict | None = None) -> dict:
    """
    Make a direct request to an OpenAlex endpoint and return the JSON payload.

    Args:
        endpoint: OpenAlex endpoint path, e.g. "works" or "works/W2741809807"
        params: Optional query params dictionary
    """
    return papers_helper.request_openalex_api(endpoint=endpoint, params=params)


@mcp.tool
@log_mcp_tool_call
def search_user_papers(user_id: str, query: str = "", limit: int = 20) -> dict:
    """
    Search papers connected to one user through collections or reading progress.

    Args:
        user_id: Application user id from the users table
        query: Optional text query to match title/abstract
        limit: Maximum papers to return (1-100)
    """
    return papers_helper.search_user_papers(user_id=user_id, query=query, limit=limit)


if __name__ == "__main__":
    port = int(os.getenv("DATABRICKS_APP_PORT", os.getenv("PORT", 8000)))

    logger.info("Initializing MCP log tables...")
    ensure_log_table_exists()
    papers_helper.ensure_openalex_request_log_table_exists()

    logger.info("Starting Papers MCP server on port %s", port)
    logger.info("Available tools:")
    logger.info("  1. search_topic(topic, per_page=10, min_citations=0)")
    logger.info("  2. get_paper_summary(openalex_id)")
    logger.info("  3. request_openalex_api(endpoint, params=None)")
    logger.info("  4. search_user_papers(user_id, query='', limit=20)")

    mcp.run(transport="http", host="0.0.0.0", port=port)
