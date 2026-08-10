# AI Research Assistant

A Flask + Lakebase (Postgres) application for goal-based research reading workflows.

The app lets users create learning goals, discover papers from OpenAlex or semantic search, organize papers into collections, maintain reading plans, and track notes/progress. It also includes a daily ingestion notebook pipeline that can add new papers per learning goal and generate embeddings.

## Core Capabilities

- User onboarding and session login
- Learning goal creation and goal-level reading plans
- Reading plan refresh and append-only sync with candidate selection
- Collections management with optional learning-goal linkage
- Paper workspace with status updates and personal notes
- OpenAlex ingestion with idempotent upserts
- Semantic retrieval with pgvector + sentence-transformers embeddings
- Daily notebook pipeline for recurring ingestion

## Architecture

```mermaid
flowchart LR
    U[User Browser] --> F[Flask App app.py]

    subgraph Web Layer
      F --> T[HTML Templates]
      F --> S[Session Auth]
      F --> R[REST/JSON APIs]
    end

    F --> L[lakebase.py]
    L --> P[(Lakebase Postgres)]

    F --> O[OpenAlexClient]
    O --> OA[(OpenAlex API)]

    F --> E[EmbeddingGenerator]
    E --> ST[SentenceTransformer all-MiniLM-L6-v2]

    subgraph Data Layer
      P --> TB1[users]
      P --> TB2[learning_goals]
      P --> TB3[collections]
      P --> TB4[papers]
      P --> TB5[reading_progress]
      P --> TB6[notes]
      P --> TB7[paper_chunks]
      P --> TB8[paper_authors]
      P --> TB9[venues]
    end

    subgraph Daily Pipeline
      N[pipeline/data_pipeline.ipynb] --> OA
      N --> ST
      N --> P
    end
```

## Request and Data Flows

### 1) Initialization flow

1. User creates account and starts initialization.
2. App creates a learning goal.
3. App searches OpenAlex for papers.
4. User selects papers.
5. App ingests papers idempotently and creates a reading plan + goal collection.

### 2) Learning-goal reading plan refresh flow

1. User opens a goal reading plan page.
2. App fetches sync candidates (local first, OpenAlex fallback).
3. User selects candidates.
4. App appends selected papers to reading plan (does not remove existing papers).

### 3) Daily pipeline flow

1. Notebook reads active learning goals.
2. For each goal, notebook searches OpenAlex.
3. Notebook reconstructs abstract and creates embeddings.
4. Notebook upserts papers by `openalex_id` and updates links (`collection_papers`, `reading_progress`).
5. Notebook raises failure if processing errors are detected.

## Repository Structure

- `app.py`: Main Flask app, route handlers, schema bootstrap, reading-plan logic.
- `lakebase.py`: Databricks secret-based Postgres connection helpers.
- `agent_helper_functions.py`: OpenAlex client, embedding generation, ingestion/chunking utilities.
- `pipeline/data_pipeline.ipynb`: Daily ingestion and embedding pipeline notebook.
- `database/ddl/00_create_all_tables.sql`: Canonical schema and vector indexes.
- `templates/`: Jinja templates for login, dashboard, goals, collections, reading plan, paper pages.
- `app.yaml`: Databricks App run command and environment configuration.
- `requirements.txt`: Python dependencies.

## Data Model Summary

The schema is OpenAlex-compatible and optimized for semantic retrieval.

- `users`: app users
- `learning_goals`: user goals
- `collections`: paper groupings (optionally linked to a goal)
- `papers`: OpenAlex work metadata + embeddings
- `venues`: publication venues/sources
- `authors` + `paper_authors`: author graph
- `collection_papers`: collection-to-paper junction
- `reading_progress`: per-user reading state and ordering
- `notes`: per-user notes (with optional embeddings)
- `paper_chunks`: chunked paper text for granular retrieval

### Idempotency points

- Papers: `ON CONFLICT (openalex_id) DO UPDATE`
- Collection links: `ON CONFLICT (collection_id, paper_id) DO NOTHING`
- Reading status links: `ON CONFLICT (user_id, paper_id) ...`

## Key Routes

### Web pages

- `/login`
- `/dashboard`
- `/learning-goals`
- `/learning-goals/<goal_id>/reading-plan`
- `/collections`
- `/paper/<paper_id>`
- `/paper-workspace/<paper_id>`

### JSON APIs (selected)

- `POST /api/users`
- `POST /api/login`
- `POST /api/initialize/search`
- `POST /api/initialize/create-plan`
- `POST /api/initialize/ingest`
- `POST /api/paper/update-status`
- `POST /api/paper/save-note`
- `POST /api/learning-goals/<goal_id>/create-reading-plan`
- `GET /api/learning-goals/<goal_id>/sync-candidates`
- `POST /api/learning-goals/<goal_id>/add-papers`
- `POST /api/collections/create`

## Environment and Secrets

From `app.yaml` and code:

- `LAKEBASE_SECRET_SCOPE` (default `database`)
- `LAKEBASE_SECRET_KEY` (default `lakebase-url`)
- `OPENALEXAPI_API_BASE_URL` (default `https://api.openalex.org/`)
- `OPENALEXAPI_SECRET_SCOPE` (default `openalex`)
- `OPENALEXAPI_SECRET_KEY` (default `api-key`)
- `FLASK_SECRET_KEY` (recommended in production)

`lakebase-url` should decode to a full Postgres URL, for example:

`postgresql://role:password@host:5432/databricks_postgres?sslmode=require`

## Local Run

1. Install dependencies:
   - `pip install -r requirements.txt`
2. Ensure Databricks auth/session is available for secret resolution.
3. Run:
   - `python app.py`
4. Open:
   - `http://0.0.0.0:8000` (or your configured host/port)

## Daily Notebook Pipeline

Notebook path: `pipeline/data_pipeline.ipynb`

The notebook includes a runnable main routine (`main_daily_pipeline`) intended for scheduled execution.

Supported environment toggles:

- `PIPELINE_USER_ID`: optional; run for one user only
- `PIPELINE_PER_GOAL_LIMIT`: default `25`
- `PIPELINE_EMBED_NEW_ONLY`: default `true`
- `PIPELINE_DRY_RUN`: default `false`

Recommended schedule: run once daily in your Databricks jobs/workflows setup.

## Notes

- The app bootstraps schema at startup (`initialize_all_tables`) if permissions allow.
- The paper workspace external link prioritizes DOI, then OpenAlex URL, then landing page URL.
- No dedicated test suite is currently included in this repository.
