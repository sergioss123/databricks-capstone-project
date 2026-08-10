"""Helper functions for the Papers MCP server.

This module keeps business logic separate from the MCP transport layer.
"""

import base64
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import psycopg2
import requests
from databricks.sdk import WorkspaceClient
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

REQUEST_LOG_TABLE = "paper_mcp_openalex_request_logs"

_workspace_client = WorkspaceClient()
_lakebase_scope = os.environ.get("LAKEBASE_SECRET_SCOPE", "database")
_lakebase_key = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")

# Cache embedding generator to avoid multiple initializations
_embedding_generator_cache = None


def _lakebase_url() -> str:
    secret = _workspace_client.secrets.get_secret(scope=_lakebase_scope, key=_lakebase_key)
    return base64.b64decode(secret.value).decode("utf-8")


@contextmanager
def get_connection():
    conn = psycopg2.connect(_lakebase_url(), cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def run_query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def run_write(sql: str, params: tuple | dict | None = None) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount


def run_write_returning(sql: str, params: tuple | dict | None = None) -> list[dict]:
    """Execute INSERT/UPDATE with RETURNING clause and commit."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            result = cur.fetchall()
            conn.commit()
            return result


class OpenAlexClient:
    """Lightweight OpenAlex client local to the MCP folder."""

    def __init__(self, per_page: int = 25):
        self.base_url = os.environ.get("OPENALEXAPI_API_BASE_URL", "https://api.openalex.org/").rstrip("/")
        secret_scope = os.environ.get("OPENALEXAPI_SECRET_SCOPE", "openalex")
        secret_key = os.environ.get("OPENALEXAPI_SECRET_KEY", "api-key")

        self.api_key = None
        try:
            secret = _workspace_client.secrets.get_secret(scope=secret_scope, key=secret_key)
            if secret and secret.value:
                self.api_key = base64.b64decode(secret.value).decode("utf-8")
        except Exception as e:
            logger.warning("Could not retrieve OpenAlex API key from Databricks secrets: %s", e)

        self.per_page = min(per_page, 200)
        self.session = requests.Session()

        if self.api_key:
            self.session.headers.update({"Authorization": f"Bearer {self.api_key}"})

    def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/{endpoint}"
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error("Error calling OpenAlex endpoint %s: %s", endpoint, e)
            return None

    def search_works(
        self,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        sort: Optional[str] = None,
        page: int = 1,
        per_page: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        results_per_page = min(per_page or self.per_page, 200)
        params: Dict[str, Any] = {"per-page": results_per_page, "page": page}

        if query:
            params["search"] = query

        if filters:
            filter_parts = []
            for key, value in filters.items():
                if isinstance(value, bool):
                    filter_parts.append(f"{key}:{str(value).lower()}")
                elif isinstance(value, (list, tuple)):
                    filter_parts.append(f"{key}:{'|'.join(map(str, value))}")
                else:
                    filter_parts.append(f"{key}:{value}")
            params["filter"] = ",".join(filter_parts)

        if sort:
            params["sort"] = sort

        return self._make_request("works", params)

    def get_work(self, work_id: str) -> Optional[Dict[str, Any]]:
        if work_id.startswith("https://openalex.org/"):
            work_id = work_id.split("/")[-1]
        elif work_id.startswith("https://doi.org/"):
            work_id = f"doi:{work_id.split('doi.org/')[-1]}"

        return self._make_request(f"works/{work_id}")

    @staticmethod
    def reconstruct_abstract(inverted_index: Dict[str, list[int]]) -> str:
        if not inverted_index:
            return ""

        word_positions = []
        for word, positions in inverted_index.items():
            for pos in positions:
                word_positions.append((pos, word))

        word_positions.sort()
        return " ".join([word for _, word in word_positions])

    def search_by_topic(
        self,
        topic_query: str,
        publication_year_start: Optional[int] = None,
        publication_year_end: Optional[int] = None,
        min_citations: Optional[int] = None,
        is_oa: Optional[bool] = None,
        page: int = 1,
        per_page: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        filters: Dict[str, Any] = {}

        if publication_year_start and publication_year_end:
            filters["publication_year"] = f"{publication_year_start}-{publication_year_end}"
        elif publication_year_start:
            filters["from_publication_date"] = f"{publication_year_start}-01-01"
        elif publication_year_end:
            filters["to_publication_date"] = f"{publication_year_end}-12-31"

        if min_citations:
            filters["cited_by_count"] = f">{min_citations}"

        if is_oa is not None:
            filters["is_oa"] = is_oa

        return self.search_works(
            query=topic_query,
            filters=filters if filters else None,
            sort="cited_by_count:desc",
            page=page,
            per_page=per_page,
        )


def summarize_abstract(abstract: str, max_length: int = 280) -> str:
    """Generate a short preview from abstract text."""
    if not abstract:
        return ""
    cleaned = abstract.strip().replace("\n", " ")
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[:max_length].rsplit(" ", 1)[0] + "..."


class PapersMCPHelperFunctions:
    """Encapsulates paper MCP business logic and data access."""

    def __init__(self, per_page: int = 25):
        self.openalex_client = OpenAlexClient(per_page=per_page)

    @staticmethod
    def _normalize_openalex_id(openalex_id: Optional[str]) -> Optional[str]:
        if not openalex_id:
            return None
        clean = openalex_id.strip()
        if not clean:
            return None
        return clean.replace("https://openalex.org/", "")

    def _table_has_column(self, table_name: str, column_name: str) -> bool:
        rows = run_query(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = %s AND column_name = %s
            ) AS present
            """,
            (table_name, column_name),
        )
        return bool(rows and rows[0].get("present"))

    def _upsert_paper_from_openalex_work(self, work: dict) -> Optional[str]:
        """Upsert a paper row from OpenAlex work payload and return local paper id."""
        if not work:
            return None

        raw_openalex_id = work.get("id")
        openalex_id = self._normalize_openalex_id(raw_openalex_id)
        title = work.get("title") or work.get("display_name")
        if not openalex_id or not title:
            return None

        existing = run_query("SELECT id FROM papers WHERE openalex_id = %s LIMIT 1", (openalex_id,))
        if existing:
            run_write(
                """
                UPDATE papers
                SET doi = COALESCE(%s, doi),
                    title = COALESCE(%s, title),
                    display_name = COALESCE(%s, display_name),
                    abstract = COALESCE(%s, abstract),
                    publication_year = COALESCE(%s, publication_year),
                    cited_by_count = COALESCE(%s, cited_by_count),
                    landing_page_url = COALESCE(%s, landing_page_url),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    work.get("doi"),
                    title,
                    work.get("display_name"),
                    self.openalex_client.reconstruct_abstract(work.get("abstract_inverted_index") or {}),
                    work.get("publication_year"),
                    work.get("cited_by_count"),
                    (work.get("primary_location") or {}).get("landing_page_url"),
                    existing[0]["id"],
                ),
            )
            return existing[0]["id"]

        inserted = run_write_returning(
            """
            INSERT INTO papers (
                openalex_id,
                doi,
                title,
                display_name,
                abstract,
                publication_year,
                cited_by_count,
                landing_page_url
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                openalex_id,
                work.get("doi"),
                title,
                work.get("display_name"),
                self.openalex_client.reconstruct_abstract(work.get("abstract_inverted_index") or {}),
                work.get("publication_year"),
                work.get("cited_by_count", 0),
                (work.get("primary_location") or {}).get("landing_page_url"),
            ),
        )
        return inserted[0]["id"] if inserted else None

    def _ensure_goal_collection(
        self,
        user_id: str,
        goal_id: str,
        goal_title: str,
        goal_description: Optional[str],
    ) -> Optional[str]:
        collections_have_goal = self._table_has_column("collections", "learning_goal_id")
        existing = run_query(
            """
            SELECT id FROM collections
            WHERE user_id = %s AND name = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, goal_title),
        )

        if existing:
            collection_id = existing[0]["id"]
            if collections_have_goal:
                run_write(
                    """
                    UPDATE collections
                    SET learning_goal_id = %s,
                        description = COALESCE(NULLIF(%s, ''), description),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (goal_id, goal_description or "", collection_id),
                )
            return collection_id

        if collections_have_goal:
            created = run_write_returning(
                """
                INSERT INTO collections (user_id, learning_goal_id, name, description)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, goal_id, goal_title, goal_description),
            )
        else:
            created = run_write_returning(
                """
                INSERT INTO collections (user_id, name, description)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (user_id, goal_title, goal_description),
            )

        return created[0]["id"] if created else None

    def _resolve_name_matches(self, name_value: str) -> tuple[list[dict], Optional[str]]:
        """Resolve a user by name with exact-first, partial-second matching."""
        exact_rows = run_query(
            "SELECT id, email, name FROM users WHERE LOWER(name) = LOWER(%s) ORDER BY id LIMIT 3",
            (name_value,),
        )
        if len(exact_rows) == 1:
            return exact_rows, None
        if len(exact_rows) > 1:
            return exact_rows, "Multiple users found for this name. Use user_id or user_email."

        like_value = f"%{name_value}%"
        partial_rows = run_query(
            "SELECT id, email, name FROM users WHERE name ILIKE %s ORDER BY id LIMIT 3",
            (like_value,),
        )
        if len(partial_rows) == 1:
            return partial_rows, None
        if len(partial_rows) > 1:
            return partial_rows, "Multiple users matched this name fragment. Use user_id or user_email."

        return [], None

    def _resolve_user(
        self,
        user_id: str = "",
        user_email: str = "",
        user_name: str = "",
    ) -> dict:
        """Resolve one user by id/email/name with backward-compatible fallback behavior."""
        user_id = (user_id or "").strip()
        user_email = (user_email or "").strip()
        user_name = (user_name or "").strip()

        if not user_id and not user_email and not user_name:
            return {
                "ok": False,
                "result": {
                    "status": "error",
                    "message": "Provide one identifier: user_id, user_email, or user_name",
                },
            }

        resolved_by = None
        user_rows: list[dict] = []

        if user_id:
            resolved_by = "user_id"
            user_rows = run_query(
                "SELECT id, email, name FROM users WHERE id = %s LIMIT 1",
                (user_id,),
            )

            if not user_rows:
                if "@" in user_id:
                    user_rows = run_query(
                        "SELECT id, email, name FROM users WHERE LOWER(email) = LOWER(%s) LIMIT 1",
                        (user_id,),
                    )
                    if user_rows:
                        resolved_by = "user_email_fallback"
                else:
                    name_rows, name_error = self._resolve_name_matches(user_id)
                    if name_error:
                        return {
                            "ok": False,
                            "result": {
                                "status": "error",
                                "message": name_error,
                                "matches": [
                                    {
                                        "id": row.get("id"),
                                        "email": row.get("email"),
                                        "name": row.get("name"),
                                    }
                                    for row in name_rows
                                ],
                            },
                        }
                    if name_rows:
                        user_rows = name_rows
                        resolved_by = "user_name_fallback"
        elif user_email:
            resolved_by = "user_email"
            user_rows = run_query(
                "SELECT id, email, name FROM users WHERE LOWER(email) = LOWER(%s) LIMIT 1",
                (user_email,),
            )
        else:
            resolved_by = "user_name"
            user_rows, name_error = self._resolve_name_matches(user_name)
            if name_error:
                return {
                    "ok": False,
                    "result": {
                        "status": "error",
                        "message": name_error,
                        "matches": [
                            {
                                "id": row.get("id"),
                                "email": row.get("email"),
                                "name": row.get("name"),
                            }
                            for row in user_rows
                        ],
                    },
                }

        if not user_rows:
            if user_id:
                detail = user_id
            elif user_email:
                detail = user_email
            else:
                detail = user_name
            return {
                "ok": False,
                "result": {"status": "error", "message": f"User not found: {detail}"},
            }

        user_row = user_rows[0]
        return {
            "ok": True,
            "resolved_by": resolved_by,
            "user": {
                "id": str(user_row.get("id")),
                "email": user_row.get("email"),
                "name": user_row.get("name"),
            },
        }

    def ensure_openalex_request_log_table_exists(self) -> bool:
        try:
            run_write(
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
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]],
        response_status: str,
        response_size_bytes: Optional[int],
        duration_ms: int,
        error_message: Optional[str] = None,
    ) -> None:
        try:
            run_write(
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

    def search_topic(self, topic: str, per_page: int = 10, min_citations: int = 0) -> dict:
        """Search papers by topic in OpenAlex."""
        if not topic or not topic.strip():
            return {"status": "error", "message": "topic is required"}

        per_page = max(1, min(per_page, 25))
        min_citations = max(0, min_citations)

        response = self.openalex_client.search_by_topic(
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
                abstract_text = self.openalex_client.reconstruct_abstract(work["abstract_inverted_index"])
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

    def get_paper_summary(self, openalex_id: str) -> dict:
        """Get a concise summary for a paper from OpenAlex."""
        if not openalex_id or not openalex_id.strip():
            return {"status": "error", "message": "openalex_id is required"}

        work = self.openalex_client.get_work(openalex_id.strip())
        if not work:
            return {"status": "error", "message": "Paper not found in OpenAlex"}

        abstract_text = ""
        if work.get("abstract_inverted_index"):
            abstract_text = self.openalex_client.reconstruct_abstract(work["abstract_inverted_index"])

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

    def request_openalex_api(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """Make a direct request to an OpenAlex endpoint and return payload."""
        if not endpoint or not endpoint.strip():
            return {"status": "error", "message": "endpoint is required"}

        clean_endpoint = endpoint.strip().lstrip("/")
        query_params = params or {}

        started = time.time()
        try:
            response = self.openalex_client._make_request(clean_endpoint, query_params)
            elapsed_ms = int((time.time() - started) * 1000)

            response_size = len(json.dumps(response)) if response is not None else 0
            self._log_openalex_request(
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
            self._log_openalex_request(
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

    def search_user_papers(
        self,
        user_id: str = "",
        user_email: str = "",
        user_name: str = "",
        query: str = "",
        limit: int = 20,
    ) -> dict:
        """Find papers associated with a user resolved by id, email, or name."""
        query = (query or "").strip()
        limit = max(1, min(limit, 100))

        resolution = self._resolve_user(user_id=user_id, user_email=user_email, user_name=user_name)
        if not resolution.get("ok"):
            return resolution["result"]

        resolved_by = resolution["resolved_by"]
        user_data = resolution["user"]
        resolved_user_id = user_data["id"]

        params = [resolved_user_id, resolved_user_id]
        query_clause = ""
        if query:
            query_clause = " AND (p.title ILIKE %s OR COALESCE(p.abstract, '') ILIKE %s)"
            like_query = f"%{query}%"
            params.extend([like_query, like_query])

        params.append(limit)

        papers = run_query(
            f"""
            SELECT
                p.id AS paper_id,
                p.openalex_id,
                p.title,
                p.abstract,
                p.publication_year,
                p.cited_by_count,
                p.doi,
                p.landing_page_url,
                MAX(rp.status) AS reading_status,
                BOOL_OR(c.id IS NOT NULL) AS in_collection,
                BOOL_OR(rp.user_id IS NOT NULL) AS in_reading_plan,
                MAX(COALESCE(rp.updated_at, cp.added_at, p.updated_at, p.created_at)) AS last_interaction
            FROM papers p
            LEFT JOIN collection_papers cp ON cp.paper_id = p.id
            LEFT JOIN collections c ON c.id = cp.collection_id AND c.user_id = %s
            LEFT JOIN reading_progress rp ON rp.paper_id = p.id AND rp.user_id = %s
            WHERE (c.id IS NOT NULL OR rp.user_id IS NOT NULL)
            {query_clause}
            GROUP BY p.id, p.openalex_id, p.title, p.abstract, p.publication_year, p.cited_by_count, p.doi, p.landing_page_url
            ORDER BY last_interaction DESC NULLS LAST, p.cited_by_count DESC NULLS LAST
            LIMIT %s
            """,
            tuple(params),
        )

        formatted = []
        for row in papers:
            formatted.append(
                {
                    "paper_id": row.get("paper_id"),
                    "openalex_id": row.get("openalex_id"),
                    "title": row.get("title"),
                    "publication_year": row.get("publication_year"),
                    "cited_by_count": row.get("cited_by_count", 0),
                    "doi": row.get("doi"),
                    "landing_page_url": row.get("landing_page_url"),
                    "reading_status": row.get("reading_status") or "not_started",
                    "in_collection": bool(row.get("in_collection")),
                    "in_reading_plan": bool(row.get("in_reading_plan")),
                    "summary": summarize_abstract(row.get("abstract") or "", max_length=280),
                }
            )

        return {
            "status": "success",
            "resolved_by": resolved_by,
            "user": user_data,
            "query": query,
            "count": len(formatted),
            "papers": formatted,
        }

    def create_collection_from_topic(
        self,
        topic: str,
        collection_name: str = "",
        collection_description: str = "",
        user_id: str = "",
        user_email: str = "",
        user_name: str = "",
        learning_goal_id: str = "",
        max_papers: int = 10,
        min_citations: int = 0,
    ) -> dict:
        """Search OpenAlex for papers on a topic and create a collection with enhanced ingestion.
        
        Args:
            topic: Search query for OpenAlex (e.g., "transformer models NLP")
            collection_name: Name for the collection (auto-generated from topic if empty)
            collection_description: Optional description for the collection
            user_id: User identifier
            user_email: User email (alternative to user_id)
            user_name: User name (alternative to user_id)
            learning_goal_id: Optional learning goal to link the collection to
            max_papers: Maximum number of papers to retrieve (1-50, default 10)
            min_citations: Minimum citation count filter (default 0)
            
        Returns:
            dict with status, collection details, and ingested papers
        """
        # Validate and normalize inputs
        topic = (topic or "").strip()
        if not topic:
            return {
                "status": "error",
                "message": "Topic is required for collection creation",
            }
        
        collection_name = (collection_name or "").strip()
        if not collection_name:
            # Auto-generate name from topic
            collection_name = topic[:50] if len(topic) <= 50 else topic[:47] + "..."
        
        collection_description = (collection_description or "").strip()
        learning_goal_id = (learning_goal_id or "").strip()
        max_papers = max(1, min(max_papers, 50))
        min_citations = max(0, min_citations)

        # Resolve user
        resolution = self._resolve_user(user_id=user_id, user_email=user_email, user_name=user_name)
        if not resolution.get("ok"):
            return resolution["result"]

        resolved_by = resolution["resolved_by"]
        user_data = resolution["user"]
        resolved_user_id = user_data["id"]

        # Search OpenAlex for papers matching the topic
        logger.info(f"Searching OpenAlex for topic: {topic}")
        search_response = self.openalex_client.search_works(
            query=topic,
            per_page=max_papers,
        )
        
        if not search_response or not search_response.get("results"):
            return {
                "status": "error",
                "message": f"No papers found for topic: {topic}",
                "resolved_by": resolved_by,
                "user": user_data,
            }
        
        # Filter by minimum citations if specified
        candidate_papers = search_response.get("results", [])
        if min_citations > 0:
            candidate_papers = [
                paper for paper in candidate_papers
                if (paper.get("cited_by_count") or 0) >= min_citations
            ]
        
        if not candidate_papers:
            return {
                "status": "error",
                "message": f"No papers found with at least {min_citations} citations",
                "resolved_by": resolved_by,
                "user": user_data,
            }

        # Check if collections table supports learning goals
        collections_have_goal = self._table_has_column("collections", "learning_goal_id")
        
        # Validate learning goal if provided
        if learning_goal_id:
            if not collections_have_goal:
                return {
                    "status": "error",
                    "message": "Learning goal linking requires collections.learning_goal_id column",
                }
            
            # Verify the learning goal exists and belongs to the user
            goal_check = run_query(
                """
                SELECT id FROM learning_goals
                WHERE id = %s AND user_id = %s
                """,
                (learning_goal_id, resolved_user_id)
            )
            
            if not goal_check:
                return {
                    "status": "error",
                    "message": f"Learning goal {learning_goal_id} not found or not owned by user",
                }

        # Create or update collection
        logger.info(f"Creating collection '{collection_name}' for user {resolved_user_id}")
        
        # Check if collection already exists with this name
        existing_collection = run_query(
            """
            SELECT id FROM collections
            WHERE user_id = %s AND name = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (resolved_user_id, collection_name)
        )
        
        if existing_collection:
            # Update existing collection
            collection_id = existing_collection[0]["id"]
            logger.info(f"Collection {collection_id} already exists, updating...")
            
            if collections_have_goal:
                run_write(
                    """
                    UPDATE collections
                    SET description = COALESCE(NULLIF(%s, ''), description),
                        learning_goal_id = COALESCE(NULLIF(%s, ''), learning_goal_id),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (collection_description, learning_goal_id or None, collection_id)
                )
            else:
                run_write(
                    """
                    UPDATE collections
                    SET description = COALESCE(NULLIF(%s, ''), description),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (collection_description, collection_id)
                )
        else:
            # Create new collection
            if collections_have_goal:
                collection_result = run_write_returning(
                    """
                    INSERT INTO collections (user_id, learning_goal_id, name, description)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (resolved_user_id, learning_goal_id or None, collection_name, collection_description)
                )
            else:
                collection_result = run_write_returning(
                    """
                    INSERT INTO collections (user_id, name, description)
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (resolved_user_id, collection_name, collection_description)
                )
            
            collection_id = collection_result[0]["id"]
            logger.info(f"Created new collection {collection_id}")

        # Ingest papers and add to collection
        papers_ingested = 0
        papers_info = []
        
        logger.info(f"Ingesting {len(candidate_papers[:max_papers])} papers into collection...")

        for paper_data in candidate_papers[:max_papers]:
            work = paper_data.get("work_data") or paper_data
            if not work:
                continue

            paper_id = paper_data.get("paper_id")
            raw_openalex_id = work.get("id") or paper_data.get("openalex_id")
            normalized_openalex_id = self._normalize_openalex_id(raw_openalex_id)

            if not paper_id and isinstance(work.get("id"), str) and not work.get("id", "").startswith("https://openalex.org/"):
                paper_id = work.get("id")

            if not paper_id and normalized_openalex_id:
                existing = run_query("SELECT id FROM papers WHERE openalex_id = %s LIMIT 1", (normalized_openalex_id,))
                if existing:
                    paper_id = existing[0]["id"]

            if not paper_id and normalized_openalex_id:
                # Try the new enhanced ingestion first
                try:
                    # Lazy import to avoid circular dependency
                    from paper_ingestion_helpers import PaperIngestion
                    
                    paper_id = PaperIngestion.ingest_from_openalex(
                        openalex_work=work,
                        upsert=True,
                        generate_chunks=True
                    )
                    if paper_id:
                        logger.info(f"Paper {paper_id} ingested with full metadata and embeddings")
                except Exception as ingestion_error:
                    logger.warning(f"Enhanced ingestion failed, falling back to basic upsert: {ingestion_error}")
                    paper_id = self._upsert_paper_from_openalex_work(work)

            if not paper_id:
                continue

            # Add paper to collection
            run_write(
                """
                INSERT INTO collection_papers (collection_id, paper_id)
                VALUES (%s, %s)
                ON CONFLICT (collection_id, paper_id) DO NOTHING
                """,
                (collection_id, paper_id),
            )

            papers_ingested += 1
            papers_info.append(
                {
                    "paper_id": paper_id,
                    "openalex_id": normalized_openalex_id,
                    "title": paper_data.get("title") or work.get("title") or work.get("display_name"),
                    "publication_year": paper_data.get("publication_year") or work.get("publication_year"),
                    "cited_by_count": paper_data.get("cited_by_count") or work.get("cited_by_count"),
                    "doi": paper_data.get("doi") or work.get("doi"),
                }
            )

        if papers_ingested == 0:
            return {
                "status": "error",
                "message": "No papers could be ingested into the collection",
                "resolved_by": resolved_by,
                "user": user_data,
                "collection": {
                    "id": collection_id,
                    "name": collection_name,
                },
            }

        logger.info(f"Successfully ingested {papers_ingested} papers into collection {collection_id}")
        
        return {
            "status": "success",
            "message": f"Created collection '{collection_name}' with {papers_ingested} papers",
            "resolved_by": resolved_by,
            "user": user_data,
            "collection": {
                "id": collection_id,
                "name": collection_name,
                "description": collection_description,
                "learning_goal_id": learning_goal_id if learning_goal_id else None,
            },
            "search": {
                "topic": topic,
                "papers_found": len(candidate_papers),
                "papers_ingested": papers_ingested,
                "max_papers": max_papers,
                "min_citations": min_citations,
            },
            "papers": papers_info,
        }
