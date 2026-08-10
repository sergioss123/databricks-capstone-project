"""
Papers MCP server.

Exposes MCP tools backed by agent_helper_functions for:
- Searching papers by topic
- Getting paper summaries
- Requesting OpenAlex API endpoints

Run locally:
    python papers_mcp_server.py
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

from fastmcp import FastMCP

import lakebase
from mcp_tool_decorator import ensure_log_table_exists, log_mcp_tool_call

# Reuse project-level agent helpers.
CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_helper_functions import OpenAlexClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("papers-mcp-server")

openalex_client = OpenAlexClient(per_page=25)

REQUEST_LOG_TABLE = "paper_mcp_openalex_request_logs"

mcp = FastMCP("papers-service")


def summarize_abstract(abstract: str, max_length: int = 280) -> str:
    """Generate a short preview from abstract text."""
    if not abstract:
        return ""
    cleaned = abstract.strip().replace("\n", " ")
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[:max_length].rsplit(" ", 1)[0] + "..."


def ensure_openalex_request_log_table_exists() -> bool:
    try:
        lakebase.run_write(
            f"""
            CREATE TABLE IF NOT EXISTS {REQUEST_LOG_TABLE} (
                id BIGSERIAL PRIMARY KEY,
                endpoint TEXT NOT NULL,
                request_params JSONB,
                response_status TEXT NOT NULL,
                response_size_bytes INTEGER,
                duration_ms INTEGER,
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        return True
    except Exception as e:
        logger.error("Failed creating %s: %s", REQUEST_LOG_TABLE, e, exc_info=True)
        return False


def _log_openalex_request(
    endpoint: str,
    params: Dict[str, Any] | None,
    response_status: str,
    response_size_bytes: int | None,
    duration_ms: int,
    error_message: str | None = None,
) -> None:
    try:
        lakebase.run_write(
            f"""
            INSERT INTO {REQUEST_LOG_TABLE} (
                endpoint,
                request_params,
                response_status,
                response_size_bytes,
                duration_ms,
                error_message
            )
            VALUES (%s, %s::jsonb, %s, %s, %s, %s)
            """,
            (
                endpoint,
                json.dumps(params or {}),
                response_status,
                response_size_bytes,
                duration_ms,
                error_message,
            ),
        )
    except Exception as e:
        logger.error("Could not write OpenAlex request log: %s", e)


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
    if not topic or not topic.strip():
        return {"status": "error", "message": "topic is required"}

    per_page = max(1, min(per_page, 25))
    min_citations = max(0, min_citations)

    response = openalex_client.search_by_topic(
        topic_query=topic.strip(),
        min_citations=min_citations if min_citations > 0 else None,
        per_page=per_page,
    )

    if not response or "results" not in response:
        return {"status": "error", "message": "OpenAlex search failed"}

    papers = []
    for work in response.get("results", []):
        abstract_text = ""
        if work.get("abstract_inverted_index"):
            abstract_text = openalex_client.reconstruct_abstract(work["abstract_inverted_index"])
        papers.append(
            {
                "openalex_id": work.get("id"),
                "title": work.get("title") or work.get("display_name"),
                "publication_year": work.get("publication_year"),
                "cited_by_count": work.get("cited_by_count", 0),
                "doi": work.get("doi"),
                "landing_page_url": (work.get("primary_location") or {}).get("landing_page_url"),
                "summary": summarize_abstract(abstract_text, max_length=280),
            }
        )

    return {
        "status": "success",
        "topic": topic,
        "count": len(papers),
        "papers": papers,
    }


@mcp.tool
@log_mcp_tool_call
def get_paper_summary(openalex_id: str) -> dict:
    """
    Get a concise summary for a paper from OpenAlex.

    Args:
        openalex_id: OpenAlex work id or URL, for example "W2741809807"
    """
    if not openalex_id or not openalex_id.strip():
        return {"status": "error", "message": "openalex_id is required"}

    work = openalex_client.get_work(openalex_id.strip())
    if not work:
        return {"status": "error", "message": "Paper not found in OpenAlex"}

    abstract_text = ""
    if work.get("abstract_inverted_index"):
        abstract_text = openalex_client.reconstruct_abstract(work["abstract_inverted_index"])

    return {
        "status": "success",
        "openalex_id": work.get("id"),
        "title": work.get("title") or work.get("display_name"),
        "publication_year": work.get("publication_year"),
        "doi": work.get("doi"),
        "landing_page_url": (work.get("primary_location") or {}).get("landing_page_url"),
        "summary": summarize_abstract(abstract_text, max_length=420),
        "abstract_length": len(abstract_text),
    }


@mcp.tool
@log_mcp_tool_call
def request_openalex_api(endpoint: str, params: dict | None = None) -> dict:
    """
    Make a direct request to an OpenAlex endpoint and return the JSON payload.

    Args:
        endpoint: OpenAlex endpoint path, e.g. "works" or "works/W2741809807"
        params: Optional query params dictionary
    """
    if not endpoint or not endpoint.strip():
        return {"status": "error", "message": "endpoint is required"}

    clean_endpoint = endpoint.strip().lstrip("/")
    query_params = params or {}

    started = time.time()
    try:
        response = openalex_client._make_request(clean_endpoint, query_params)
        elapsed_ms = int((time.time() - started) * 1000)

        response_size = len(json.dumps(response)) if response is not None else 0
        _log_openalex_request(
            endpoint=clean_endpoint,
            params=query_params,
            response_status="success" if response else "empty",
            response_size_bytes=response_size,
            duration_ms=elapsed_ms,
        )

        if response is None:
            return {
                "status": "error",
                "message": "OpenAlex request returned no response",
                "endpoint": clean_endpoint,
                "params": query_params,
            }

        return {
            "status": "success",
            "endpoint": clean_endpoint,
            "params": query_params,
            "response": response,
        }
    except Exception as e:
        elapsed_ms = int((time.time() - started) * 1000)
        _log_openalex_request(
            endpoint=clean_endpoint,
            params=query_params,
            response_status="error",
            response_size_bytes=None,
            duration_ms=elapsed_ms,
            error_message=str(e),
        )
        return {
            "status": "error",
            "message": f"OpenAlex request failed: {str(e)}",
            "endpoint": clean_endpoint,
            "params": query_params,
        }


if __name__ == "__main__":
    port = int(os.getenv("DATABRICKS_APP_PORT", os.getenv("PORT", 8000)))

    logger.info("Initializing MCP log tables...")
    ensure_log_table_exists()
    ensure_openalex_request_log_table_exists()

    logger.info("Starting Papers MCP server on port %s", port)
    logger.info("Available tools:")
    logger.info("  1. search_topic(topic, per_page=10, min_citations=0)")
    logger.info("  2. get_paper_summary(openalex_id)")
    logger.info("  3. request_openalex_api(endpoint, params=None)")

    mcp.run(transport="http", host="0.0.0.0", port=port)
