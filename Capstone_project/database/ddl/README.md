# AI Research and Learning Copilot - Database Schema

This folder contains the DDL (Data Definition Language) scripts for the AI Research and Learning Copilot application.

## Quick Start

To create all tables in the correct order, run:
```sql
-- Execute the master script
\i 00_create_all_tables.sql
```

Or run each script individually in numerical order (01 through 09).

## Database Schema Overview

The database consists of 9 tables organized into 4 functional groups:

### 1. Core Tables (Independent)
* **users** - User account information
* **papers** - Research paper metadata
* **authors** - Author information

### 2. User Feature Tables (Depend on users)
* **learning_goals** - User learning objectives
* **collections** - User-created paper collections

### 3. Junction Tables (Many-to-Many Relationships)
* **paper_authors** - Links papers to their authors
* **collection_papers** - Links collections to papers

### 4. User Activity Tracking Tables
* **reading_progress** - Tracks user progress on papers
* **notes** - User notes and annotations on papers

---

## Table Relationships

### Entity Relationship Diagram (Conceptual)

```
users (1) ----< (M) learning_goals
  |                    
  |---- (1) ----< (M) collections
  |                       |
  |                       | (M)
  |                       |
  |---- (1) ----< (M) collection_papers >---- (M) papers
  |                                               |
  |                                               | (M)
  |                                               |
  |---- (1) ----< (M) reading_progress >---- (M) papers
  |                                               |
  |                                               | (M)
  |                                               |
  |---- (1) ----< (M) notes -------------->---- (M) papers
                                                  |
                                                  | (M)
                                                  |
                                          paper_authors
                                                  |
                                                  | (M)
                                                  |
                                               authors
```

### Detailed Relationship Descriptions

#### 1. Users and Their Data

**users → learning_goals** (One-to-Many)
* One user can have multiple learning goals
* Each learning goal belongs to exactly one user
* CASCADE DELETE: When a user is deleted, all their learning goals are deleted

**users → collections** (One-to-Many)
* One user can create multiple collections
* Each collection belongs to exactly one user
* CASCADE DELETE: When a user is deleted, all their collections are deleted

**users → reading_progress** (One-to-Many)
* One user can track progress on multiple papers
* Each progress record belongs to exactly one user
* CASCADE DELETE: When a user is deleted, all their reading progress is deleted
* UNIQUE constraint on (user_id, paper_id): A user can only have one progress record per paper

**users → notes** (One-to-Many)
* One user can create multiple notes
* Each note belongs to exactly one user
* CASCADE DELETE: When a user is deleted, all their notes are deleted

#### 2. Papers and Their Relationships

**papers ↔ authors** (Many-to-Many through paper_authors)
* One paper can have multiple authors
* One author can write multiple papers
* The junction table `paper_authors` stores:
  * `author_order`: The position of the author in the author list (1st, 2nd, etc.)
  * `is_corresponding`: Whether this author is the corresponding author
* CASCADE DELETE: When a paper or author is deleted, the relationship is removed

**papers ↔ collections** (Many-to-Many through collection_papers)
* One paper can be in multiple collections
* One collection can contain multiple papers
* The junction table `collection_papers` stores:
  * `added_at`: Timestamp when the paper was added to the collection
  * `notes`: Optional notes about why this paper is in this collection
* CASCADE DELETE: When a collection or paper is deleted, the relationship is removed

**papers → reading_progress** (One-to-Many)
* One paper can be tracked by multiple users
* Each progress record tracks one user's progress on one paper
* Stores: status, progress_percentage, last_read_at, time_spent_minutes
* CASCADE DELETE: When a paper is deleted, all reading progress for it is deleted

**papers → notes** (One-to-Many)
* One paper can have multiple notes from different users
* Each note is about exactly one paper
* Stores: content, highlight_text, page_number, is_public
* CASCADE DELETE: When a paper is deleted, all notes about it are deleted

---

## Table Details

### users
Stores user account information.

**Columns:**
* `id` (PK): UUID as text
* `email`: Unique email address
* `name`: User's display name
* `created_at`: Account creation timestamp
* `updated_at`: Last profile update timestamp

**Indexes:**
* `idx_users_email`: Fast email lookups for authentication

---

### learning_goals
Stores user learning objectives and goals.

**Columns:**
* `id` (PK): UUID as text
* `user_id` (FK → users): Owner of this learning goal
* `title`: Short title of the learning goal
* `description`: Detailed description
* `status`: Current status (active, completed, archived)
* `created_at`: Creation timestamp
* `updated_at`: Last modification timestamp

**Indexes:**
* `idx_learning_goals_user_id`: Fast user lookups
* `idx_learning_goals_status`: Filter by status

**Use Case:**
* "Learn about transformer architectures"
* "Understand quantum computing basics"
* "Master reinforcement learning algorithms"

---

### papers
Stores research paper metadata.

**Columns:**
* `id` (PK): UUID as text
* `title`: Paper title
* `abstract`: Paper abstract/summary
* `doi`: Digital Object Identifier (unique)
* `url`: Link to the paper
* `publication_date`: When the paper was published
* `venue`: Where it was published (journal, conference)
* `keywords`: JSON array of keywords/topics
* `citation_count`: Number of citations
* `created_at`: When added to our system
* `updated_at`: Last metadata update

**Indexes:**
* `idx_papers_doi`: Fast DOI lookups
* `idx_papers_publication_date`: Sort by date
* `idx_papers_keywords`: GIN index for JSON keyword searches

---

### authors
Stores author information.

**Columns:**
* `id` (PK): UUID as text
* `name`: Author's name
* `affiliation`: Institution/organization
* `h_index`: Research impact metric
* `created_at`: When added to our system
* `updated_at`: Last information update

**Indexes:**
* `idx_authors_name`: Fast name searches

---

### paper_authors
Junction table linking papers to their authors (many-to-many).

**Columns:**
* `paper_id` (PK, FK → papers): The paper
* `author_id` (PK, FK → authors): The author
* `author_order`: Position in author list (1 = first author)
* `is_corresponding`: True if this is the corresponding author
* `created_at`: When the relationship was created

**Composite Primary Key:** (paper_id, author_id)

**Indexes:**
* `idx_paper_authors_paper_id`: Get all authors for a paper
* `idx_paper_authors_author_id`: Get all papers by an author

---

### collections
User-created collections of papers.

**Columns:**
* `id` (PK): UUID as text
* `user_id` (FK → users): Collection owner
* `name`: Collection name
* `description`: What this collection is about
* `is_public`: Whether other users can see this collection
* `created_at`: Creation timestamp
* `updated_at`: Last modification timestamp

**Indexes:**
* `idx_collections_user_id`: Get all collections for a user
* `idx_collections_is_public`: Find public collections

**Use Case:**
* "Papers for my PhD literature review"
* "Must-read transformer papers"
* "Quantum computing fundamentals"

---

### collection_papers
Junction table linking collections to papers (many-to-many).

**Columns:**
* `collection_id` (PK, FK → collections): The collection
* `paper_id` (PK, FK → papers): The paper
* `added_at`: When the paper was added to the collection
* `notes`: Why this paper is in this collection (optional)

**Composite Primary Key:** (collection_id, paper_id)

**Indexes:**
* `idx_collection_papers_collection_id`: Get all papers in a collection
* `idx_collection_papers_paper_id`: Find which collections contain a paper
* `idx_collection_papers_added_at`: Sort papers by when they were added

---

### reading_progress
Tracks user reading progress on papers.

**Columns:**
* `id` (PK): UUID as text
* `user_id` (FK → users): The user
* `paper_id` (FK → papers): The paper
* `status`: not_started, in_progress, or completed
* `progress_percentage`: 0-100
* `last_read_at`: Last time the user read this paper
* `time_spent_minutes`: Total time spent reading
* `created_at`: When tracking started
* `updated_at`: Last progress update

**Unique Constraint:** (user_id, paper_id) - One progress record per user per paper

**Indexes:**
* `idx_reading_progress_user_id`: Get all progress for a user
* `idx_reading_progress_paper_id`: See who's reading a paper
* `idx_reading_progress_status`: Filter by reading status

---

### notes
User notes and annotations on papers.

**Columns:**
* `id` (PK): UUID as text
* `user_id` (FK → users): Note author
* `paper_id` (FK → papers): Paper being annotated
* `content`: The note content (markdown supported)
* `highlight_text`: Text the user highlighted (optional)
* `page_number`: Which page this note refers to (optional)
* `is_public`: Whether other users can see this note
* `created_at`: Note creation time
* `updated_at`: Last edit time

**Indexes:**
* `idx_notes_user_id`: Get all notes by a user
* `idx_notes_paper_id`: Get all notes on a paper
* `idx_notes_created_at`: Sort notes chronologically

---

## Data Flow Example

### Scenario: A user discovers and studies a paper

1. **User signs up**
   * Record created in `users` table

2. **User creates a learning goal**
   * "Master attention mechanisms in deep learning"
   * Record created in `learning_goals` linked to user

3. **User discovers a paper**
   * "Attention Is All You Need" (Vaswani et al., 2017)
   * Record created in `papers` table
   * Authors created in `authors` table (if not exists)
   * Relationships created in `paper_authors` table

4. **User creates a collection**
   * "Transformer Architecture Papers"
   * Record created in `collections` linked to user

5. **User adds paper to collection**
   * Relationship created in `collection_papers`
   * Includes a note: "The foundational transformer paper"

6. **User starts reading**
   * Record created in `reading_progress`
   * Status: "in_progress", progress_percentage: 0%

7. **User takes notes while reading**
   * Multiple records created in `notes`
   * Each note linked to the user and paper
   * Some notes marked as public for sharing

8. **User updates progress**
   * `reading_progress` updated: progress_percentage: 45%
   * `time_spent_minutes` incremented
   * `last_read_at` timestamp updated

9. **User completes the paper**
   * `reading_progress` updated: status: "completed", progress_percentage: 100%

10. **AI agent creates study plan**
    * Queries the user's `learning_goals`
    * Finds related papers in the user's `collections`
    * Checks `reading_progress` to see what's completed
    * Reviews `notes` to understand user's interests
    * Generates personalized recommendations

---

## Design Decisions

### Why UUID as TEXT for Primary Keys?
* **Distributed system friendly**: UUIDs can be generated client-side
* **No collision risk**: Safe for concurrent inserts
* **Anonymization**: No sequential IDs that leak information
* **External API ready**: Safe to expose in URLs

### Why JSONB for keywords?
* **Flexible schema**: Papers can have varying numbers of keywords
* **Efficient queries**: PostgreSQL GIN indexes support fast keyword searches
* **No additional tables**: Avoids a separate keywords table for simple use cases

### Why separate paper_authors junction table?
* **Author order matters**: Captures first author, second author, etc.
* **Corresponding author**: Important in academic context
* **Clean separation**: Authors exist independently of papers

### Why both collections and reading_progress?
* **Different purposes**:
  * **collections**: Organization and curation ("Papers I want to read")
  * **reading_progress**: Activity tracking ("Papers I'm currently reading")
* A paper can be in a collection without being read yet
* Users can track progress on papers not in any collection

### Cascade DELETE Strategy
* **User deletion**: Removes all user data (GDPR compliance)
* **Paper deletion**: Removes all references and user data about that paper
* **Collection deletion**: Only removes the collection, not the papers themselves
* **Author deletion**: Rare, but removes authorship links only

---

---

## Vector Embeddings for AI Agent

The schema includes vector embedding support for semantic search and RAG (Retrieval-Augmented Generation).

### Embeddings Overview

Embeddings are added to:
* **papers.abstract_embedding** - Semantic representation of paper abstracts
* **papers.content_embedding** - Full paper content (when available)
* **learning_goals.description_embedding** - Learning objective descriptions
* **notes.content_embedding** - User note content
* **collections.description_embedding** - Collection descriptions

### Setup Instructions

1. **Enable pgvector extension**
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

2. **Run embedding setup**
   ```sql
   \i 10_embeddings_setup.sql
   ```

3. **Create vector indexes**
   ```sql
   \i 11_embedding_indexes.sql
   ```

### Embedding Generation

Use the provided Python helper functions (`agent_helper_functions.py`):

```python
from agent_helper_functions import EmbeddingGenerator, PaperIngestion

# Initialize
embedding_gen = EmbeddingGenerator(api_key="your-openai-api-key")
paper_ingestion = PaperIngestion(db_connection, embedding_gen)

# Ingest a new paper with automatic embedding generation
paper_id = paper_ingestion.ingest_paper(
    title="Attention Is All You Need",
    abstract="The dominant sequence transduction models...",
    authors=[{"name": "Vaswani, A.", "affiliation": "Google"}]
)
```

### Agent Capabilities

The schema supports these AI agent features:

#### 1. Find Papers Matching a Learning Goal
Semantic search to discover relevant papers for a learning objective.

```python
from agent_helper_functions import AgentCapabilities

agent = AgentCapabilities(db_connection, embedding_gen)
papers = agent.find_papers_for_goal(goal_id="...", limit=10)
```

#### 2. Retrieve Evidence Across Multiple Papers
RAG-style retrieval instead of sending entire collection to the model.

```python
evidence = agent.retrieve_evidence_from_collection(
    query="What are the limitations of transformer architectures?",
    collection_id="...",
    limit=5
)
```

#### 3. Search User Notes
Find what the user has already learned about a topic.

```python
relevant_notes = agent.search_user_notes(
    query="attention mechanism",
    user_id="...",
    limit=5
)
```

#### 4. Generate Sequenced Reading Plan
Create a personalized study sequence based on:
* Relevance to learning goal
* Paper difficulty (citation count proxy)
* Current reading progress

```python
reading_plan = agent.generate_reading_plan(
    goal_id="...",
    user_id="..."
)
```

#### 5. Recommend Next Paper
Suggest the next paper to read based on progress and goals.

```python
next_paper = agent.recommend_next_paper(
    user_id="...",
    goal_id="..."
)
```

#### 6. Hybrid Search
Combine keyword and semantic search (30% keyword, 70% semantic).

```python
results = agent.hybrid_search(
    query_text="transformer attention mechanisms",
    limit=10
)
```

### Query Examples

See `12_agent_queries.sql` for complete SQL query examples for all agent capabilities.

### Performance Considerations

* **HNSW indexes**: Provide fast approximate nearest neighbor search
* **Index parameters**: Tuned for balance of speed and accuracy (m=16, ef_construction=64)
* **Query tuning**: Adjust `hnsw.ef_search` for accuracy vs speed tradeoff
* **Batch operations**: Use batch embedding generation for better throughput

### Embedding Models

Default: OpenAI `text-embedding-ada-002` (1536 dimensions)

To use different models, adjust:
1. Vector dimension in DDL files (e.g., `vector(768)` for sentence-transformers)
2. Model name in Python helper functions
3. Regenerate all embeddings

---

## Future Enhancements

Potential additions to the schema:

* **citations** table: Track which papers cite which (paper citation graph)
* **study_plans** table: AI-generated personalized study plans linked to learning goals
* **recommendations** table: Paper recommendation history and feedback
* **user_follows** table: Users following other users
* **paper_versions** table: Track paper revisions (e.g., arXiv v1, v2, v3)
* **reading_sessions** table: Detailed time-series reading analytics
* **tags** table: User-defined tags in addition to paper keywords
* **feedback** table: User ratings and feedback on papers and recommendations
* **conversation_history** table: Store agent conversation context