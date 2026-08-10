# Papers MCP Bot Usage Process

This guide defines how a bot should use the Papers MCP tools step-by-step.

## 1. Identify Intent
Map user request to one primary intent:
- Topic discovery -> `search_topic`
- Single paper explanation -> `get_paper_summary`
- User library/plan lookup -> `search_user_papers`
- Raw OpenAlex payload request -> `request_openalex_api`

## 2. Validate Required Inputs
- For topic discovery: require `topic`.
- For paper summary: require `openalex_id`.
- For user lookup: require one of `user_id`, `user_email`, or `user_name`.
- For raw OpenAlex: require `endpoint`.

If missing, ask one targeted clarification question before calling tools.

## 3. Execute Tool Calls
### A) Topic Discovery
1. Call `search_topic(topic, per_page, min_citations)`.
2. Return top results with title, year, citations, and OpenAlex ID.
3. Offer to summarize one result via `get_paper_summary`.

### B) Paper Summary
1. Call `get_paper_summary(openalex_id)`.
2. Return title, year, DOI/link, and concise summary.

### C) User Papers
1. Call `search_user_papers(user_id, user_email, user_name, query, limit)`.
2. Return counts and list by recency/relevance from tool output.
3. Highlight reading status and collection membership.
4. If name resolves to multiple users, ask for `user_id` or `user_email`.

### D) Create Reading Plan
1. Call `create_user_reading_plan(user_id, user_email, user_name, goal_id, goal_title, papers, max_papers, persist)`.
2. Always provide goal context with `goal_id` or `goal_title`.
3. When available, pass `papers` from the search step to replicate the initialize button flow (top oldest papers).
4. Return goal, collection, and ordered reading plan entries.
5. If `persist=true`, confirm that reading order was written to `reading_progress`.

### E) Raw OpenAlex
1. Call `request_openalex_api(endpoint, params)`.
2. Summarize key fields unless user requested full JSON.

## 4. Handle Errors
- If `status = error`, show:
  - tool name
  - error message
  - one concrete retry option

Example: "`search_user_papers` returned 'User not found'. Verify `user_id`, `user_email`, or `user_name` and retry."

## 5. Output Style
- Keep output short and actionable.
- Use flat bullet lists.
- Include exact IDs for follow-up actions.
- End with one next step (optional).

## 6. Example Flows
### Example 1: "Find me transformer papers with high citations"
1. Call `search_topic(topic="transformer", per_page=10, min_citations=500)`.
2. Present top papers.
3. Ask which `openalex_id` to summarize.

### Example 2: "Show my papers about reinforcement learning"
1. Require one of `user_id`, `user_email`, or `user_name`.
2. Call `search_user_papers(user_email="user@company.com", query="reinforcement learning", limit=20)`.
3. Return matching items and statuses.
