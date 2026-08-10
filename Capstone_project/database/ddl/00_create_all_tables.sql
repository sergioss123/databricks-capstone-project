-- Master DDL script for AI Research and Learning Copilot
-- Optimized for OpenAlex API data structure
-- Run this script to create all tables in the correct order
-- Tables are created in dependency order to respect foreign key constraints

-- ===========================
-- 1. Core Tables (no dependencies)
-- ===========================

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

-- Venues/Sources table (journals, conferences, repositories)
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
);
CREATE INDEX IF NOT EXISTS idx_venues_openalex_id ON venues (openalex_id);
CREATE INDEX IF NOT EXISTS idx_venues_issn_l ON venues (issn_l);
CREATE INDEX IF NOT EXISTS idx_venues_type ON venues (type);

-- Papers/Works table (OpenAlex-compatible)
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
);
CREATE INDEX IF NOT EXISTS idx_papers_openalex_id ON papers (openalex_id);
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers (doi);
CREATE INDEX IF NOT EXISTS idx_papers_publication_date ON papers (publication_date);
CREATE INDEX IF NOT EXISTS idx_papers_publication_year ON papers (publication_year);
CREATE INDEX IF NOT EXISTS idx_papers_type ON papers (type);
CREATE INDEX IF NOT EXISTS idx_papers_is_oa ON papers (is_oa);
CREATE INDEX IF NOT EXISTS idx_papers_oa_status ON papers (oa_status);
CREATE INDEX IF NOT EXISTS idx_papers_cited_by_count ON papers (cited_by_count);
CREATE INDEX IF NOT EXISTS idx_papers_keywords ON papers USING GIN (keywords);
CREATE INDEX IF NOT EXISTS idx_papers_concepts ON papers USING GIN (concepts);
CREATE INDEX IF NOT EXISTS idx_papers_topics ON papers USING GIN (topics);

-- Authors table (OpenAlex-compatible)
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
);
CREATE INDEX IF NOT EXISTS idx_authors_openalex_id ON authors (openalex_id);
CREATE INDEX IF NOT EXISTS idx_authors_display_name ON authors (display_name);
CREATE INDEX IF NOT EXISTS idx_authors_orcid ON authors (orcid);

-- ===========================
-- 2. Tables with single FK dependencies
-- ===========================

-- Learning goals table (depends on users)
CREATE TABLE IF NOT EXISTS learning_goals (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'archived')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_learning_goals_user_id ON learning_goals (user_id);
CREATE INDEX IF NOT EXISTS idx_learning_goals_status ON learning_goals (status);

-- Collections table (depends on users)
CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    is_public BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_collections_user_id ON collections (user_id);
CREATE INDEX IF NOT EXISTS idx_collections_is_public ON collections (is_public);

-- ===========================
-- 3. Junction tables (many-to-many relationships)
-- ===========================

-- Paper authors junction table (OpenAlex authorships - depends on papers and authors)
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
);
CREATE INDEX IF NOT EXISTS idx_paper_authors_paper_id ON paper_authors (paper_id);
CREATE INDEX IF NOT EXISTS idx_paper_authors_author_id ON paper_authors (author_id);
CREATE INDEX IF NOT EXISTS idx_paper_authors_position ON paper_authors (author_position);

-- Collection papers junction table (depends on collections and papers)
CREATE TABLE IF NOT EXISTS collection_papers (
    collection_id TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes TEXT,
    PRIMARY KEY (collection_id, paper_id),
    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_collection_papers_collection_id ON collection_papers (collection_id);
CREATE INDEX IF NOT EXISTS idx_collection_papers_paper_id ON collection_papers (paper_id);
CREATE INDEX IF NOT EXISTS idx_collection_papers_added_at ON collection_papers (added_at);

-- ===========================
-- 4. User activity tracking tables
-- ===========================

-- Reading progress table (depends on users and papers)
CREATE TABLE IF NOT EXISTS reading_progress (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    status TEXT DEFAULT 'not_started' CHECK (status IN ('not_started', 'in_progress', 'completed')),
    progress_percentage INTEGER DEFAULT 0 CHECK (progress_percentage >= 0 AND progress_percentage <= 100),
    last_read_at TIMESTAMPTZ,
    time_spent_minutes INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, paper_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reading_progress_user_id ON reading_progress (user_id);
CREATE INDEX IF NOT EXISTS idx_reading_progress_paper_id ON reading_progress (paper_id);
CREATE INDEX IF NOT EXISTS idx_reading_progress_status ON reading_progress (status);

-- Notes table (depends on users and papers)
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
);
CREATE INDEX IF NOT EXISTS idx_notes_user_id ON notes (user_id);
CREATE INDEX IF NOT EXISTS idx_notes_paper_id ON notes (paper_id);
CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes (created_at);

-- ===========================
-- 5. Vector Embeddings Setup
-- ===========================

-- Enable pgvector extension for vector similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- Add embedding columns to papers (1024 dimensions for databricks-bge-large-en)
ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_embedding vector(1024);
ALTER TABLE papers ADD COLUMN IF NOT EXISTS content_embedding vector(1024);
ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_embedding_model TEXT DEFAULT 'text-embedding-ada-002';
ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_embedding_generated_at TIMESTAMPTZ;

-- Add embedding columns to learning_goals
ALTER TABLE learning_goals ADD COLUMN IF NOT EXISTS description_embedding vector(1024);
ALTER TABLE learning_goals ADD COLUMN IF NOT EXISTS embedding_model TEXT DEFAULT 'text-embedding-ada-002';
ALTER TABLE learning_goals ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ;

-- Add embedding columns to notes
ALTER TABLE notes ADD COLUMN IF NOT EXISTS content_embedding vector(1024);
ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding_model TEXT DEFAULT 'text-embedding-ada-002';
ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ;

-- Add embedding columns to collections
ALTER TABLE collections ADD COLUMN IF NOT EXISTS description_embedding vector(1024);
ALTER TABLE collections ADD COLUMN IF NOT EXISTS embedding_model TEXT DEFAULT 'text-embedding-ada-002';
ALTER TABLE collections ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ;

-- ===========================
-- 6. Vector Similarity Indexes (HNSW)
-- ===========================

-- Papers abstract embeddings index
CREATE INDEX IF NOT EXISTS idx_papers_abstract_embedding_hnsw 
    ON papers 
    USING hnsw (abstract_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Papers content embeddings index
CREATE INDEX IF NOT EXISTS idx_papers_content_embedding_hnsw 
    ON papers 
    USING hnsw (content_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Learning goals description embeddings index
CREATE INDEX IF NOT EXISTS idx_learning_goals_description_embedding_hnsw 
    ON learning_goals 
    USING hnsw (description_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Notes content embeddings index
CREATE INDEX IF NOT EXISTS idx_notes_content_embedding_hnsw 
    ON notes 
    USING hnsw (content_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Collections description embeddings index
CREATE INDEX IF NOT EXISTS idx_collections_description_embedding_hnsw 
    ON collections 
    USING hnsw (description_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ===========================
-- 7. Paper Chunks for Granular Semantic Search
-- ===========================

-- Paper chunks table (depends on papers)
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
);

-- Indexes for paper_chunks
CREATE INDEX IF NOT EXISTS idx_paper_chunks_paper_id ON paper_chunks (paper_id);
CREATE INDEX IF NOT EXISTS idx_paper_chunks_chunk_type ON paper_chunks (chunk_type);
CREATE INDEX IF NOT EXISTS idx_paper_chunks_section_title ON paper_chunks (section_title);

-- Paper chunks embeddings HNSW index for granular semantic search
CREATE INDEX IF NOT EXISTS idx_paper_chunks_embedding_hnsw 
    ON paper_chunks 
    USING hnsw (chunk_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ===========================
-- 8. Views for Enhanced Queries
-- ===========================

-- View that joins paper metadata with chunks for comprehensive search
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
    p.url,
    p.publication_date,
    p.venue,
    p.keywords,
    p.citation_count,
    p.created_at AS paper_created_at
FROM paper_chunks pc
JOIN papers p ON pc.paper_id = p.id;