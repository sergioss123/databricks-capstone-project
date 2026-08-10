-- MCP logging tables for papers MCP server
-- Execute after 00_create_all_tables.sql

CREATE TABLE IF NOT EXISTS paper_mcp_tool_call_logs (
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
    CONSTRAINT valid_paper_mcp_status CHECK (status IN ('success', 'error', 'timeout', 'partial'))
);

CREATE INDEX IF NOT EXISTS idx_paper_mcp_tool_call_logs_tool_name
    ON paper_mcp_tool_call_logs (tool_name);

CREATE INDEX IF NOT EXISTS idx_paper_mcp_tool_call_logs_called_at
    ON paper_mcp_tool_call_logs (called_at DESC);

CREATE INDEX IF NOT EXISTS idx_paper_mcp_tool_call_logs_user_email
    ON paper_mcp_tool_call_logs (user_email);

CREATE TABLE IF NOT EXISTS paper_mcp_openalex_request_logs (
    id BIGSERIAL PRIMARY KEY,
    endpoint TEXT NOT NULL,
    request_params JSONB,
    response_status TEXT NOT NULL,
    response_size_bytes INTEGER,
    duration_ms INTEGER,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_paper_mcp_openalex_request_logs_endpoint
    ON paper_mcp_openalex_request_logs (endpoint);

CREATE INDEX IF NOT EXISTS idx_paper_mcp_openalex_request_logs_created_at
    ON paper_mcp_openalex_request_logs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_paper_mcp_openalex_request_logs_response_status
    ON paper_mcp_openalex_request_logs (response_status);
