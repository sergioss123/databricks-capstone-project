"""
Databricks App boilerplate:
- Serves a small Flask API
- Reads/writes to Lakebase (Databricks-managed Postgres) via lakebase.py
- Pulls data from the Massive API via massive_client.py and syncs it into Lakebase

Run locally:
    python app.py
Deploy as a Databricks App using app.yaml.
"""

import logging
import os
import re
import json
from datetime import datetime

import requests
from databricks.sdk import WorkspaceClient
from flask import Flask, jsonify, render_template, request, session, redirect, url_for

import lakebase
from agent_helper_functions import OpenAlexClient, PaperIngestion

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("capston-project-app")

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
_w = WorkspaceClient()

# Initialize OpenAlex client
openalex_client = OpenAlexClient()

# ===========================
# Database Initialization Functions
# ===========================

def ensure_pgvector_extension():
    """Enable pgvector extension for vector similarity search."""
    lakebase.run_write("CREATE EXTENSION IF NOT EXISTS vector")


# ===========================
# 1. Core Tables (no dependencies)
# ===========================

def ensure_users_table():
    """Create the users table in Lakebase if it doesn't exist yet."""
    lakebase.run_write(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_users_email ON users (email)"
    )


def ensure_venues_table():
    """Create the venues/sources table (journals, conferences, repositories)."""
    lakebase.run_write(
        """
        CREATE TABLE IF NOT EXISTS venues (
            id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            openalex_id TEXT UNIQUE,
            display_name TEXT NOT NULL,
            issn_l TEXT,
            issn JSONB,
            is_oa BOOLEAN DEFAULT false,
            is_in_doaj BOOLEAN DEFAULT false,
            type TEXT,
            host_organization TEXT,
            homepage_url TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_venues_openalex_id ON venues (openalex_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_venues_issn_l ON venues (issn_l)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_venues_type ON venues (type)"
    )


def ensure_papers_table():
    """Create the papers/works table (OpenAlex-compatible)."""
    lakebase.run_write(
        """
        CREATE TABLE IF NOT EXISTS papers (
            id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            openalex_id TEXT UNIQUE,
            doi TEXT,
            title TEXT NOT NULL,
            display_name TEXT,
            abstract TEXT,
            abstract_inverted_index JSONB,
            publication_year INTEGER,
            publication_date DATE,
            type TEXT,
            language TEXT,
            venue_id TEXT,
            venue_display_name TEXT,
            is_oa BOOLEAN DEFAULT false,
            oa_status TEXT,
            pdf_url TEXT,
            landing_page_url TEXT,
            cited_by_count INTEGER DEFAULT 0,
            biblio JSONB,
            keywords JSONB,
            concepts JSONB,
            topics JSONB,
            mesh JSONB,
            sustainable_development_goals JSONB,
            referenced_works_count INTEGER DEFAULT 0,
            referenced_works JSONB,
            is_retracted BOOLEAN DEFAULT false,
            is_paratext BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (venue_id) REFERENCES venues(id) ON DELETE SET NULL
        )
        """
    )
    # Create indexes
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_papers_openalex_id ON papers (openalex_id)",
        "CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers (doi)",
        "CREATE INDEX IF NOT EXISTS idx_papers_publication_date ON papers (publication_date)",
        "CREATE INDEX IF NOT EXISTS idx_papers_publication_year ON papers (publication_year)",
        "CREATE INDEX IF NOT EXISTS idx_papers_type ON papers (type)",
        "CREATE INDEX IF NOT EXISTS idx_papers_is_oa ON papers (is_oa)",
        "CREATE INDEX IF NOT EXISTS idx_papers_oa_status ON papers (oa_status)",
        "CREATE INDEX IF NOT EXISTS idx_papers_cited_by_count ON papers (cited_by_count)",
        "CREATE INDEX IF NOT EXISTS idx_papers_keywords ON papers USING GIN (keywords)",
        "CREATE INDEX IF NOT EXISTS idx_papers_concepts ON papers USING GIN (concepts)",
        "CREATE INDEX IF NOT EXISTS idx_papers_topics ON papers USING GIN (topics)",
    ]
    for index in indexes:
        lakebase.run_write(index)


def ensure_authors_table():
    """Create the authors table (OpenAlex-compatible)."""
    lakebase.run_write(
        """
        CREATE TABLE IF NOT EXISTS authors (
            id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            openalex_id TEXT UNIQUE,
            display_name TEXT NOT NULL,
            orcid TEXT,
            works_count INTEGER DEFAULT 0,
            cited_by_count INTEGER DEFAULT 0,
            last_known_institutions JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_authors_openalex_id ON authors (openalex_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_authors_display_name ON authors (display_name)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_authors_orcid ON authors (orcid)"
    )


# ===========================
# 2. Tables with Single FK Dependencies
# ===========================

def ensure_learning_goals_table():
    """Create the learning goals table (depends on users)."""
    lakebase.run_write(
        """
        CREATE TABLE IF NOT EXISTS learning_goals (
            id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'archived')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_learning_goals_user_id ON learning_goals (user_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_learning_goals_status ON learning_goals (status)"
    )


def ensure_collections_table():
    """Create the collections table (depends on users)."""
    lakebase.run_write(
        """
        CREATE TABLE IF NOT EXISTS collections (
            id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            is_public BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_collections_user_id ON collections (user_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_collections_is_public ON collections (is_public)"
    )


# ===========================
# 3. Junction Tables (Many-to-Many)
# ===========================

def ensure_paper_authors_table():
    """Create the paper authors junction table (OpenAlex authorships)."""
    lakebase.run_write(
        """
        CREATE TABLE IF NOT EXISTS paper_authors (
            paper_id TEXT NOT NULL,
            author_id TEXT NOT NULL,
            author_order INTEGER NOT NULL,
            author_position TEXT,
            is_corresponding BOOLEAN DEFAULT false,
            raw_author_name TEXT,
            raw_affiliation_strings JSONB,
            institutions JSONB,
            countries JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (paper_id, author_id),
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
            FOREIGN KEY (author_id) REFERENCES authors(id) ON DELETE CASCADE
        )
        """
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_paper_authors_paper_id ON paper_authors (paper_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_paper_authors_author_id ON paper_authors (author_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_paper_authors_position ON paper_authors (author_position)"
    )


def ensure_collection_papers_table():
    """Create the collection papers junction table."""
    lakebase.run_write(
        """
        CREATE TABLE IF NOT EXISTS collection_papers (
            collection_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            notes TEXT,
            PRIMARY KEY (collection_id, paper_id),
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        )
        """
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_collection_papers_collection_id ON collection_papers (collection_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_collection_papers_paper_id ON collection_papers (paper_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_collection_papers_added_at ON collection_papers (added_at)"
    )


# ===========================
# 4. User Activity Tracking Tables
# ===========================

def ensure_reading_progress_table():
    """Create the reading progress table."""
    lakebase.run_write(
        """
        CREATE TABLE IF NOT EXISTS reading_progress (
            id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            user_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            learning_goal_id TEXT,
            reading_order INTEGER,
            status TEXT DEFAULT 'not_started' CHECK (status IN ('not_started', 'in_progress', 'completed')),
            progress_percentage INTEGER DEFAULT 0 CHECK (progress_percentage >= 0 AND progress_percentage <= 100),
            last_read_at TIMESTAMPTZ,
            time_spent_minutes INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(user_id, paper_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
            FOREIGN KEY (learning_goal_id) REFERENCES learning_goals(id) ON DELETE CASCADE
        )
        """
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_reading_progress_user_id ON reading_progress (user_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_reading_progress_paper_id ON reading_progress (paper_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_reading_progress_status ON reading_progress (status)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_reading_progress_learning_goal_id ON reading_progress (learning_goal_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_reading_progress_reading_order ON reading_progress (reading_order)"
    )


def ensure_notes_table():
    """Create the notes table."""
    lakebase.run_write(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            user_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            content TEXT NOT NULL,
            highlight_text TEXT,
            page_number INTEGER,
            is_public BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        )
        """
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_notes_user_id ON notes (user_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_notes_paper_id ON notes (paper_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes (created_at)"
    )


# ===========================
# 5. Paper Chunks for Granular Semantic Search
# ===========================

def ensure_paper_chunks_table():
    """Create the paper chunks table for granular semantic search."""
    lakebase.run_write(
        """
        CREATE TABLE IF NOT EXISTS paper_chunks (
            id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
            paper_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            chunk_type TEXT DEFAULT 'paragraph',
            section_title TEXT,
            page_number INTEGER,
            start_char INTEGER NOT NULL,
            end_char INTEGER NOT NULL,
            word_count INTEGER NOT NULL,
            chunk_embedding vector(1024),
            embedding_model TEXT DEFAULT 'databricks-bge-large-en',
            embedding_generated_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
            UNIQUE(paper_id, chunk_index)
        )
        """
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_paper_chunks_paper_id ON paper_chunks (paper_id)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_paper_chunks_chunk_type ON paper_chunks (chunk_type)"
    )
    lakebase.run_write(
        "CREATE INDEX IF NOT EXISTS idx_paper_chunks_section_title ON paper_chunks (section_title)"
    )


# ===========================
# 6. Vector Embeddings Setup
# ===========================

def ensure_vector_columns():
    """Add embedding columns to tables (1024 dimensions for databricks-bge-large-en)."""
    # Papers table embeddings
    lakebase.run_write(
        "ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_embedding vector(1024)"
    )
    lakebase.run_write(
        "ALTER TABLE papers ADD COLUMN IF NOT EXISTS content_embedding vector(1024)"
    )
    lakebase.run_write(
        "ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_embedding_model TEXT DEFAULT 'text-embedding-ada-002'"
    )
    lakebase.run_write(
        "ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_embedding_generated_at TIMESTAMPTZ"
    )
    
    # Learning goals embeddings
    lakebase.run_write(
        "ALTER TABLE learning_goals ADD COLUMN IF NOT EXISTS description_embedding vector(1024)"
    )
    lakebase.run_write(
        "ALTER TABLE learning_goals ADD COLUMN IF NOT EXISTS embedding_model TEXT DEFAULT 'text-embedding-ada-002'"
    )
    lakebase.run_write(
        "ALTER TABLE learning_goals ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ"
    )
    
    # Notes embeddings
    lakebase.run_write(
        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS content_embedding vector(1024)"
    )
    lakebase.run_write(
        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding_model TEXT DEFAULT 'text-embedding-ada-002'"
    )
    lakebase.run_write(
        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ"
    )
    
    # Collections embeddings
    lakebase.run_write(
        "ALTER TABLE collections ADD COLUMN IF NOT EXISTS description_embedding vector(1024)"
    )
    lakebase.run_write(
        "ALTER TABLE collections ADD COLUMN IF NOT EXISTS embedding_model TEXT DEFAULT 'text-embedding-ada-002'"
    )
    lakebase.run_write(
        "ALTER TABLE collections ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ"
    )


# ===========================
# 7. Vector Similarity Indexes (HNSW)
# ===========================

def ensure_vector_indexes():
    """Create HNSW indexes for vector similarity search."""
    # Papers abstract embeddings index
    lakebase.run_write(
        """
        CREATE INDEX IF NOT EXISTS idx_papers_abstract_embedding_hnsw 
            ON papers 
            USING hnsw (abstract_embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """
    )
    
    # Papers content embeddings index
    lakebase.run_write(
        """
        CREATE INDEX IF NOT EXISTS idx_papers_content_embedding_hnsw 
            ON papers 
            USING hnsw (content_embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """
    )
    
    # Learning goals description embeddings index
    lakebase.run_write(
        """
        CREATE INDEX IF NOT EXISTS idx_learning_goals_description_embedding_hnsw 
            ON learning_goals 
            USING hnsw (description_embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """
    )
    
    # Notes content embeddings index
    lakebase.run_write(
        """
        CREATE INDEX IF NOT EXISTS idx_notes_content_embedding_hnsw 
            ON notes 
            USING hnsw (content_embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """
    )
    
    # Collections description embeddings index
    lakebase.run_write(
        """
        CREATE INDEX IF NOT EXISTS idx_collections_description_embedding_hnsw 
            ON collections 
            USING hnsw (description_embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """
    )
    
    # Paper chunks embeddings index
    lakebase.run_write(
        """
        CREATE INDEX IF NOT EXISTS idx_paper_chunks_embedding_hnsw 
            ON paper_chunks 
            USING hnsw (chunk_embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """
    )


# ===========================
# 8. Views for Enhanced Queries
# ===========================

def ensure_views():
    """Create views for enhanced queries."""
    lakebase.run_write(
        """
        CREATE OR REPLACE VIEW paper_chunks_with_metadata AS
        SELECT 
            pc.id AS chunk_id,
            pc.paper_id,
            pc.chunk_index,
            pc.chunk_text,
            pc.chunk_type,
            pc.section_title,
            pc.page_number,
            pc.word_count,
            pc.start_char,
            pc.end_char,
            pc.chunk_embedding,
            pc.embedding_model AS chunk_embedding_model,
            pc.embedding_generated_at AS chunk_embedding_generated_at,
            p.title AS paper_title,
            p.abstract AS paper_abstract,
            p.doi,
            p.landing_page_url AS url,
            p.publication_date,
            p.venue_display_name AS venue,
            p.keywords,
            p.cited_by_count AS citation_count,
            p.created_at AS paper_created_at
        FROM paper_chunks pc
        JOIN papers p ON pc.paper_id = p.id
        """
    )


# ===========================
# Master Initialization Function
# ===========================

def initialize_all_tables():
    """
    Initialize all tables in the correct dependency order.
    Call this function to set up the entire database schema.
    """
    logger.info("Starting database initialization...")
    
    # Step 1: Enable pgvector extension
    logger.info("Enabling pgvector extension...")
    ensure_pgvector_extension()
    
    # Step 2: Create core tables (no dependencies)
    logger.info("Creating core tables...")
    ensure_users_table()
    ensure_venues_table()
    ensure_papers_table()
    ensure_authors_table()
    
    # Step 3: Create tables with single FK dependencies
    logger.info("Creating dependent tables...")
    ensure_learning_goals_table()
    ensure_collections_table()
    
    # Step 4: Create junction tables
    logger.info("Creating junction tables...")
    ensure_paper_authors_table()
    ensure_collection_papers_table()
    
    # Step 5: Create user activity tracking tables
    logger.info("Creating activity tracking tables...")
    ensure_reading_progress_table()
    ensure_notes_table()
    
    # Step 6: Create paper chunks table
    logger.info("Creating paper chunks table...")
    ensure_paper_chunks_table()
    
    # Step 7: Add vector embedding columns
    logger.info("Adding vector embedding columns...")
    ensure_vector_columns()
    
    # Step 8: Create vector similarity indexes
    logger.info("Creating vector similarity indexes...")
    ensure_vector_indexes()
    
    # Step 9: Create views
    logger.info("Creating views...")
    ensure_views()
    
    logger.info("Database initialization complete!")


# ===========================
# Helper Functions
# ===========================

def get_current_user():
    """Get current logged-in user from session."""
    user_id = session.get('user_id')
    if not user_id:
        return None
    
    results = lakebase.run_query(
        "SELECT id, email, name, created_at FROM users WHERE id = %s",
        (user_id,)
    )
    return results[0] if results else None

def require_login(f):
    """Decorator to require login for routes."""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def _current_user_email() -> str:
    """
    Resolve the current user's email so the watchlist can be personalized.

    Databricks Apps inject the logged-in user's identity via the
    X-Forwarded-Email header on every request. Fall back to the Databricks
    SDK's current_user API for local development where that header isn't set.
    """
    header_email = request.headers.get("X-Forwarded-Email")
    if header_email:
        return header_email
    return _w.current_user.me().user_name


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})

# ===========================
# API Routes
# ===========================

@app.route("/api/login", methods=['POST'])
def api_login():
    """Login API endpoint."""
    data = request.json
    user_id = data.get('user_id')
    
    if not user_id:
        return jsonify({'success': False, 'error': 'User ID required'}), 400
    
    # Verify user exists
    users = lakebase.run_query(
        "SELECT id FROM users WHERE id = %s",
        (user_id,)
    )
    
    if not users:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    
    # Set session
    session['user_id'] = user_id
    
    return jsonify({'success': True})

@app.route("/api/users", methods=['POST'])
def api_create_user():
    """Create new user API endpoint."""
    data = request.json
    name = data.get('name', '').strip()
    email = data.get('email', '').strip()
    
    if not name or not email:
        return jsonify({'success': False, 'error': 'Name and email required'}), 400
    
    try:
        # Check if email already exists
        existing = lakebase.run_query(
            "SELECT id FROM users WHERE email = %s",
            (email,)
        )
        
        if existing:
            return jsonify({'success': False, 'error': 'Email already exists'}), 400
        
        # Create user with explicit connection and commit
        with lakebase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (email, name)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (email, name)
                )
                result = cur.fetchone()
                conn.commit()  # Explicitly commit the transaction
        
        user_id = result['id']
        logger.info(f"Created new user: {name} ({email}) with ID {user_id}")
        
        return jsonify({'success': True, 'user_id': user_id})
    
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/api/initialize/search", methods=['POST'])
def api_initialize_search():
    """Search for papers during initialization."""
    data = request.json
    user_id = data.get('user_id')
    goal_title = data.get('goal_title')
    goal_description = data.get('goal_description', '')
    search_query = data.get('search_query')
    paper_count = data.get('paper_count', 25)
    
    if not user_id or not goal_title or not search_query:
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400
    
    try:
        # First verify user exists
        user_check = lakebase.run_query(
            "SELECT id FROM users WHERE id = %s",
            (user_id,)
        )
        if not user_check:
            return jsonify({'success': False, 'error': f'User {user_id} not found in database'}), 404
        
        # Create learning goal with explicit connection and commit
        with lakebase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO learning_goals (user_id, title, description, status)
                    VALUES (%s, %s, %s, 'active')
                    RETURNING id
                    """,
                    (user_id, goal_title, goal_description)
                )
                goal_result = cur.fetchone()
                conn.commit()  # Explicitly commit the transaction
        
        goal_id = goal_result['id']
        logger.info(f"Created learning goal {goal_id} for user {user_id}")
        
        # Search OpenAlex for papers
        logger.info(f"Searching OpenAlex for: {search_query}")
        response = openalex_client.search_works(
            query=search_query,
            per_page=paper_count
        )
        
        if not response or 'results' not in response:
            return jsonify({'success': False, 'error': 'Search failed'}), 500
        
        papers = []
        for work in response['results']:
            # Reconstruct abstract if it's in inverted index format
            abstract_text = ''
            if work.get('abstract_inverted_index'):
                abstract_text = openalex_client.reconstruct_abstract(
                    work['abstract_inverted_index']
                )
            
            papers.append({
                'openalex_id': work.get('id'),
                'title': work.get('title') or work.get('display_name'),
                'display_name': work.get('display_name'),
                'abstract_text': abstract_text,
                'publication_year': work.get('publication_year'),
                'cited_by_count': work.get('cited_by_count', 0),
                'is_oa': work.get('open_access', {}).get('is_oa', False),
                'work_data': work  # Store full work data for ingestion
            })
        
        logger.info(f"Found {len(papers)} papers")
        
        return jsonify({
            'success': True,
            'papers': papers,
            'goal_id': goal_id
        })
    
    except Exception as e:
        logger.error(f"Error in initialize search: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/api/initialize/create-plan", methods=['POST'])
def api_initialize_create_plan():
    """Create a reading plan from the top 10 papers, ordered by date (oldest first)."""
    data = request.json
    user_id = data.get('user_id')
    goal_id = data.get('goal_id')
    papers = data.get('papers', [])
    
    if not user_id or not goal_id or not papers:
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400
    
    try:
        # Sort papers by publication_date (oldest first), take top 10
        sorted_papers = sorted(
            papers,
            key=lambda p: p.get('publication_year', 0) or 0
        )[:10]
        
        papers_ingested = 0
        reading_plan = []
        
        for order, paper_data in enumerate(sorted_papers, start=1):
            try:
                # Get full work data
                work = paper_data.get('work_data', {})
                
                if not work:
                    logger.warning(f"No work data for paper: {paper_data.get('title')}")
                    continue
                
                # Ingest paper using PaperIngestion class
                paper_id = PaperIngestion.ingest_from_openalex(
                    openalex_work=work,
                    upsert=True  # Enable idempotency
                )
                
                if paper_id:
                    # Create reading progress entry with reading order
                    with lakebase.get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO reading_progress 
                                    (user_id, paper_id, learning_goal_id, reading_order, status)
                                VALUES (%s, %s, %s, %s, 'not_started')
                                ON CONFLICT (user_id, paper_id) 
                                DO UPDATE SET 
                                    learning_goal_id = EXCLUDED.learning_goal_id,
                                    reading_order = EXCLUDED.reading_order,
                                    updated_at = now()
                                RETURNING id
                                """,
                                (user_id, paper_id, goal_id, order)
                            )
                            result = cur.fetchone()
                            conn.commit()
                    
                    papers_ingested += 1
                    reading_plan.append({
                        'paper_id': paper_id,
                        'title': paper_data.get('title'),
                        'order': order,
                        'publication_year': paper_data.get('publication_year'),
                        'status': 'not_started'
                    })
                    
                    logger.info(f"Added paper {order}/10 to reading plan: {paper_data.get('title')[:50]}")
                
            except Exception as e:
                logger.error(f"Error ingesting paper {paper_data.get('title')}: {e}")
                continue
        
        logger.info(f"Created reading plan with {papers_ingested} papers for goal {goal_id}")
        
        return jsonify({
            'success': True,
            'papers_ingested': papers_ingested,
            'reading_plan': reading_plan,
            'goal_id': goal_id
        })
    
    except Exception as e:
        logger.error(f"Error creating reading plan: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/api/initialize/ingest", methods=['POST'])
def api_initialize_ingest():
    """Ingest papers and generate embeddings."""
    data = request.json
    user_id = data.get('user_id')
    papers = data.get('papers', [])
    
    if not user_id or not papers:
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400
    
    try:
        papers_ingested = 0
        chunks_created = 0
        
        for paper_data in papers:
            try:
                # Get full work data
                work = paper_data.get('work_data', {})
                
                if not work:
                    logger.warning(f"No work data for paper: {paper_data.get('title')}")
                    continue
                
                # Ingest paper using PaperIngestion class
                result = PaperIngestion.ingest_from_openalex(
                    openalex_work=work,
                    upsert=True  # Enable idempotency
                )
                
                if result:
                    papers_ingested += 1
                    # Get chunk count for this paper
                    chunk_count = lakebase.run_query(
                        "SELECT COUNT(*) as count FROM paper_chunks WHERE paper_id = %s",
                        (result,)
                    )
                    if chunk_count:
                        chunks_created += chunk_count[0]['count']
                
            except Exception as e:
                logger.error(f"Error ingesting paper {paper_data.get('title')}: {e}")
                continue
        
        logger.info(f"Ingested {papers_ingested} papers with {chunks_created} chunks")
        
        return jsonify({
            'success': True,
            'papers_ingested': papers_ingested,
            'chunks_created': chunks_created
        })
    
    except Exception as e:
        logger.error(f"Error in initialize ingest: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route("/api/paper/update-status", methods=['POST'])
def api_update_paper_status():
    """Update reading status for a paper."""
    data = request.json
    paper_id = data.get('paper_id')
    status = data.get('status')  # 'completed', 'in_progress', 'not_started'
    user_id = session.get('user_id')
    
    if not user_id:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    
    if not paper_id or not status:
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400
    
    if status not in ['not_started', 'in_progress', 'completed']:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400
    
    try:
        # Update or create reading progress
        with lakebase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO reading_progress (user_id, paper_id, status, progress_percentage, last_read_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (user_id, paper_id)
                    DO UPDATE SET
                        status = EXCLUDED.status,
                        progress_percentage = EXCLUDED.progress_percentage,
                        last_read_at = EXCLUDED.last_read_at,
                        updated_at = now()
                    RETURNING id
                    """,
                    (user_id, paper_id, status, 100 if status == 'completed' else 0)
                )
                result = cur.fetchone()
                conn.commit()
        
        logger.info(f"Updated paper {paper_id} status to {status} for user {user_id}")
        
        return jsonify({
            'success': True,
            'status': status
        })
    
    except Exception as e:
        logger.error(f"Error updating paper status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.errorhandler(Exception)
def handle_exception(err):
    """Ensure all unhandled errors return JSON (not an HTML error page),
    so the frontend's resp.json() call never chokes on HTML."""
    logger.exception("Unhandled exception while processing request")
    status_code = getattr(err, "code", 500)
    if not isinstance(status_code, int):
        status_code = 500
    return jsonify({"error": str(err)}), status_code


# ===========================
# Routes
# ===========================

@app.route("/")
def index():
    """Redirect to login or dashboard based on session."""
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route("/login")
def login():
    """Login page - select existing user or create new one."""
    # Get all users from database
    users = lakebase.run_query(
        "SELECT id, email, name FROM users ORDER BY created_at DESC"
    )
    return render_template("login.html", users=users)

@app.route("/logout")
def logout():
    """Logout current user."""
    session.clear()
    return redirect(url_for('login'))

@app.route("/initialize")
def initialize():
    """Initialize page for new users."""
    user_id = request.args.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    
    # Verify user exists in database before setting session
    user_check = lakebase.run_query(
        "SELECT id FROM users WHERE id = %s",
        (user_id,)
    )
    if not user_check:
        logger.error(f"User {user_id} not found in database")
        return redirect(url_for('login'))
    
    # Set user in session
    session['user_id'] = user_id
    return render_template("initialize.html")

@app.route("/dashboard")
@require_login
def dashboard():
    """Main dashboard for logged-in users."""
    user = get_current_user()
    
    # If user not found in database, clear session and redirect to login
    if not user:
        session.clear()
        return redirect(url_for('login'))
    
    # Get stats
    stats = {
        'total_papers': lakebase.run_query(
            "SELECT COUNT(*) as count FROM papers"
        )[0]['count'],
        'learning_goals': lakebase.run_query(
            "SELECT COUNT(*) as count FROM learning_goals WHERE user_id = %s",
            (user['id'],)
        )[0]['count'],
        'reading_progress': 0,  # Calculate average later
        'collections': lakebase.run_query(
            "SELECT COUNT(*) as count FROM collections WHERE user_id = %s",
            (user['id'],)
        )[0]['count']
    }
    
    # Get learning goals
    learning_goals = lakebase.run_query(
        """
        SELECT id, title, description, status
        FROM learning_goals
        WHERE user_id = %s AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 3
        """,
        (user['id'],)
    )
    
    # Add progress to each goal (dummy data for now)
    for goal in learning_goals:
        goal['progress'] = 50  # TODO: Calculate actual progress
    
    # Get recommended papers (top cited papers for now)
    recommended_papers = lakebase.run_query(
        """
        SELECT id, title, abstract, publication_year, cited_by_count, is_oa
        FROM papers
        ORDER BY cited_by_count DESC
        LIMIT 5
        """
    )
    
    return render_template(
        "dashboard.html",
        active_tab='dashboard',
        current_user=user,
        stats=stats,
        learning_goals=learning_goals,
        recommended_papers=recommended_papers,
        recent_activity=[]  # TODO: Implement activity tracking
    )

@app.route("/papers")
@require_login
def papers():
    """Papers library page."""
    user = get_current_user()
    if not user:
        session.clear()
        return redirect(url_for('login'))
    # TODO: Implement papers page
    return "Papers page coming soon!"

@app.route("/learning-goals")
@require_login
def learning_goals():
    """Learning goals page with reading plans."""
    user = get_current_user()
    if not user:
        session.clear()
        return redirect(url_for('login'))
    
    # Get all learning goals for the user
    goals = lakebase.run_query(
        """
        SELECT id, title, description, status, created_at
        FROM learning_goals
        WHERE user_id = %s
        ORDER BY created_at DESC
        """,
        (user['id'],)
    )
    
    # For each goal, get the reading plan
    for goal in goals:
        reading_plan = lakebase.run_query(
            """
            SELECT 
                rp.id,
                rp.paper_id,
                rp.reading_order,
                rp.status,
                rp.progress_percentage,
                rp.last_read_at,
                p.title,
                p.abstract,
                p.publication_year,
                p.publication_date,
                p.doi,
                p.landing_page_url,
                p.cited_by_count
            FROM reading_progress rp
            JOIN papers p ON rp.paper_id = p.id
            WHERE rp.user_id = %s AND rp.learning_goal_id = %s
            ORDER BY rp.reading_order ASC
            """,
            (user['id'], goal['id'])
        )
        goal['reading_plan'] = reading_plan
        
        # Calculate progress
        if reading_plan:
            completed = sum(1 for p in reading_plan if p['status'] == 'completed')
            goal['progress'] = int((completed / len(reading_plan)) * 100)
        else:
            goal['progress'] = 0
    
    return render_template("learning_goals.html", goals=goals, user=user)

@app.route("/collections")
@require_login
def collections():
    """Collections page."""
    user = get_current_user()
    if not user:
        session.clear()
        return redirect(url_for('login'))
    # TODO: Implement collections page
    return "Collections page coming soon!"

@app.route("/paper/<paper_id>")
@require_login
def paper_detail(paper_id):
    """Paper reading page with full content and completion tracking."""
    user = get_current_user()
    if not user:
        session.clear()
        return redirect(url_for('login'))
    
    # Get paper details
    paper_result = lakebase.run_query(
        """
        SELECT 
            p.*,
            rp.id as reading_progress_id,
            rp.status,
            rp.progress_percentage,
            rp.reading_order,
            rp.learning_goal_id,
            lg.title as goal_title
        FROM papers p
        LEFT JOIN reading_progress rp ON p.id = rp.paper_id AND rp.user_id = %s
        LEFT JOIN learning_goals lg ON rp.learning_goal_id = lg.id
        WHERE p.id = %s
        """,
        (user['id'], paper_id)
    )
    
    if not paper_result:
        return "Paper not found", 404
    
    paper = paper_result[0]
    
    # Get paper chunks for reading
    chunks = lakebase.run_query(
        """
        SELECT chunk_index, chunk_text, chunk_type, section_title, page_number
        FROM paper_chunks
        WHERE paper_id = %s
        ORDER BY chunk_index ASC
        """,
        (paper_id,)
    )
    
    paper['chunks'] = chunks
    
    # Get other papers in the same reading plan
    if paper.get('learning_goal_id'):
        reading_plan = lakebase.run_query(
            """
            SELECT 
                rp.paper_id,
                rp.reading_order,
                rp.status,
                p.title
            FROM reading_progress rp
            JOIN papers p ON rp.paper_id = p.id
            WHERE rp.learning_goal_id = %s AND rp.user_id = %s
            ORDER BY rp.reading_order ASC
            """,
            (paper['learning_goal_id'], user['id'])
        )
        paper['reading_plan'] = reading_plan
        
        # Find current position
        current_idx = next((i for i, p in enumerate(reading_plan) if p['paper_id'] == paper_id), None)
        paper['prev_paper'] = reading_plan[current_idx - 1] if current_idx and current_idx > 0 else None
        paper['next_paper'] = reading_plan[current_idx + 1] if current_idx is not None and current_idx < len(reading_plan) - 1 else None
    
    return render_template("paper_read.html", paper=paper, user=user)


if __name__ == '__main__':
    # Initialize database tables on startup
    logger.info("Initializing database tables...")
    try:
        initialize_all_tables()
        logger.info("Database initialization complete!")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        logger.warning("App will continue but some features may not work")
    
    host = os.getenv('FLASK_RUN_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_RUN_PORT', 8000))
    logger.info(f"Starting Flask app on http://{host}:{port}")
    app.run(debug=True, host=host, port=port)