-- Migration for switching embedding storage from 1024-dimension Databricks models
-- to 384-dimension sentence-transformers models.
--
-- This migration is destructive for the old embedding columns. Existing vectors
-- are dropped and must be regenerated after the schema change.

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE papers DROP COLUMN IF EXISTS abstract_embedding;
ALTER TABLE papers DROP COLUMN IF EXISTS content_embedding;
ALTER TABLE papers DROP COLUMN IF EXISTS abstract_embedding_model;
ALTER TABLE papers DROP COLUMN IF EXISTS abstract_embedding_generated_at;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_embedding vector(384);
ALTER TABLE papers ADD COLUMN IF NOT EXISTS content_embedding vector(384);
ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_embedding_model TEXT DEFAULT 'sentence-transformers/all-MiniLM-L6-v2';
ALTER TABLE papers ADD COLUMN IF NOT EXISTS abstract_embedding_generated_at TIMESTAMPTZ;

ALTER TABLE learning_goals DROP COLUMN IF EXISTS description_embedding;
ALTER TABLE learning_goals DROP COLUMN IF EXISTS embedding_model;
ALTER TABLE learning_goals DROP COLUMN IF EXISTS embedding_generated_at;
ALTER TABLE learning_goals ADD COLUMN IF NOT EXISTS description_embedding vector(384);
ALTER TABLE learning_goals ADD COLUMN IF NOT EXISTS embedding_model TEXT DEFAULT 'sentence-transformers/all-MiniLM-L6-v2';
ALTER TABLE learning_goals ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ;

ALTER TABLE notes DROP COLUMN IF EXISTS content_embedding;
ALTER TABLE notes DROP COLUMN IF EXISTS embedding_model;
ALTER TABLE notes DROP COLUMN IF EXISTS embedding_generated_at;
ALTER TABLE notes ADD COLUMN IF NOT EXISTS content_embedding vector(384);
ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding_model TEXT DEFAULT 'sentence-transformers/all-MiniLM-L6-v2';
ALTER TABLE notes ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ;

ALTER TABLE collections DROP COLUMN IF EXISTS description_embedding;
ALTER TABLE collections DROP COLUMN IF EXISTS embedding_model;
ALTER TABLE collections DROP COLUMN IF EXISTS embedding_generated_at;
ALTER TABLE collections ADD COLUMN IF NOT EXISTS description_embedding vector(384);
ALTER TABLE collections ADD COLUMN IF NOT EXISTS embedding_model TEXT DEFAULT 'sentence-transformers/all-MiniLM-L6-v2';
ALTER TABLE collections ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ;

ALTER TABLE paper_chunks DROP COLUMN IF EXISTS chunk_embedding;
ALTER TABLE paper_chunks DROP COLUMN IF EXISTS embedding_model;
ALTER TABLE paper_chunks DROP COLUMN IF EXISTS embedding_generated_at;
ALTER TABLE paper_chunks ADD COLUMN IF NOT EXISTS chunk_embedding vector(384);
ALTER TABLE paper_chunks ADD COLUMN IF NOT EXISTS embedding_model TEXT DEFAULT 'sentence-transformers/all-MiniLM-L6-v2';
ALTER TABLE paper_chunks ADD COLUMN IF NOT EXISTS embedding_generated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_papers_abstract_embedding_hnsw
    ON papers
    USING hnsw (abstract_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_papers_content_embedding_hnsw
    ON papers
    USING hnsw (content_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_learning_goals_description_embedding_hnsw
    ON learning_goals
    USING hnsw (description_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_notes_content_embedding_hnsw
    ON notes
    USING hnsw (content_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_collections_description_embedding_hnsw
    ON collections
    USING hnsw (description_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_paper_chunks_embedding_hnsw
    ON paper_chunks
    USING hnsw (chunk_embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);