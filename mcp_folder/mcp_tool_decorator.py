"""MCP Tool Call Logging Decorator for the paper MCP server."""

import json
import logging
import time
from functools import wraps
from typing import Any, Callable, Optional

from flask import has_request_context, request

try:
    import lakebase
    LAKEBASE_AVAILABLE = True
except ImportError:
    LAKEBASE_AVAILABLE = False

logger = logging.getLogger(__name__)
TABLE_NAME = "paper_mcp_tool_call_logs"


def _get_user_email() -> Optional[str]:
    if has_request_context():
        return request.headers.get("X-Forwarded-Email")
    return None


def _get_session_id() -> Optional[str]:
    if has_request_context():
        return (
            request.headers.get("X-Session-ID")
            or request.headers.get("X-Request-ID")
            or request.cookies.get("session_id")
        )
    return None


def _truncate(text: Optional[str], max_len: int = 500) -> Optional[str]:
    if text is None:
        return None
    return text[:max_len] if len(text) > max_len else text


def _insert_log(
    tool_name: str,
    input_parameters: dict,
    output_result: Any,
    execution_duration_ms: int,
    status: str,
    error_message: Optional[str],
    error_type: Optional[str],
    user_email: Optional[str],
    session_id: Optional[str],
) -> bool:
    if not LAKEBASE_AVAILABLE:
        return False

    try:
        output_json = json.dumps(output_result) if output_result is not None else None
        input_json = json.dumps(input_parameters) if input_parameters else None
        summary = _truncate(output_json)

        lakebase.run_write(
            f"""
            INSERT INTO {TABLE_NAME} (
                tool_name,
                user_email,
                session_id,
                input_parameters,
                output_result,
                result_summary,
                execution_duration_ms,
                status,
                error_message,
                error_type
            )
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)
            """,
            (
                tool_name,
                user_email,
                session_id,
                input_json,
                output_json,
                summary,
                execution_duration_ms,
                status,
                error_message,
                error_type,
            ),
        )
        return True
    except Exception as e:
        logger.error("Failed to log MCP tool call: %s", e, exc_info=True)
        return False


def log_mcp_tool_call(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        tool_name = func.__name__
        status = "success"
        error_message = None
        error_type = None
        result = None

        try:
            result = func(*args, **kwargs)
            if isinstance(result, dict) and result.get("status") == "error":
                status = "error"
                error_message = result.get("message", "Unknown error")
                error_type = "ToolError"
            return result
        except Exception as e:
            status = "error"
            error_message = str(e)
            error_type = type(e).__name__
            raise
        finally:
            elapsed_ms = int((time.time() - start) * 1000)
            _insert_log(
                tool_name=tool_name,
                input_parameters={"args": args, "kwargs": kwargs},
                output_result=result,
                execution_duration_ms=elapsed_ms,
                status=status,
                error_message=error_message,
                error_type=error_type,
                user_email=_get_user_email(),
                session_id=_get_session_id(),
            )

    return wrapper


def ensure_log_table_exists() -> bool:
    if not LAKEBASE_AVAILABLE:
        logger.warning("Cannot ensure log table: lakebase not available")
        return False

    try:
        lakebase.run_write(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id BIGSERIAL PRIMARY KEY,
                tool_name TEXT NOT NULL,
                user_email TEXT,
                session_id TEXT,
                input_parameters JSONB,
                output_result JSONB,
                result_summary TEXT,
                called_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                execution_duration_ms INTEGER,
                status TEXT NOT NULL DEFAULT 'success',
                error_message TEXT,
                error_type TEXT,
                CONSTRAINT valid_status CHECK (status IN ('success', 'error', 'timeout', 'partial'))
            )
            """
        )
        return True
    except Exception as e:
        logger.error("Failed to create %s: %s", TABLE_NAME, e, exc_info=True)
        return False
