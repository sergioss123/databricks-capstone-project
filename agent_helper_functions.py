"""Agent Helper Functions for AI Research and Learning Copilot

Provides utility functions for:
- OpenAlex API integration for paper search and retrieval
- Generating embeddings for papers, notes, and learning goals
- Text chunking for granular semantic search
- RAG (Retrieval-Augmented Generation) queries
- Agent capabilities (find papers, generate reading plans, etc.)
- Citation and reference extraction from paper chunks

All database operations use lakebase.py (Databricks-managed Postgres) for connection management.
"""

import os
import re
import requests
import time
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import lakebase
from databricks.sdk import WorkspaceClient
from sentence_transformers import SentenceTransformer


class OpenAlexClient:
    """Client for interacting with the OpenAlex API
    
    OpenAlex is a free, open catalog of scholarly papers, authors, venues, and institutions.
    API documentation: https://docs.openalex.org
    
    Authentication via Databricks secrets using:
    - OPENALEXAPI_API_BASE_URL (default: https://api.openalex.org/)
    - OPENALEXAPI_SECRET_SCOPE (default: openalex)
    - OPENALEXAPI_SECRET_KEY (default: api-key)
    """
    
    def __init__(self, per_page: int = 25):
        """
        Initialize OpenAlex API client with authentication from Databricks secrets
        
        Args:
            per_page: Results per page (max 200, default 25)
        """
        # Get configuration from environment variables
        self.base_url = os.environ.get("OPENALEXAPI_API_BASE_URL", "https://api.openalex.org/").rstrip('/')
        secret_scope = os.environ.get("OPENALEXAPI_SECRET_SCOPE", "openalex")
        secret_key = os.environ.get("OPENALEXAPI_SECRET_KEY", "api-key")
        
        # Retrieve API key from Databricks secrets
        try:
            w = WorkspaceClient()
            self.api_key = w.secrets.get_secret(scope=secret_scope, key=secret_key)
        except Exception as e:
            print(f"Warning: Could not retrieve API key from secrets: {e}")
            self.api_key = None
        
        self.per_page = min(per_page, 200)
        self.session = requests.Session()
        
        # Set up authentication header if API key is available
        if self.api_key:
            self.session.headers.update({
                "Authorization": f"Bearer {self.api_key}"
            })
        
    def _make_request(self, endpoint: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Make a request to OpenAlex API with authentication"""
        url = f"{self.base_url}/{endpoint}"
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error making request to OpenAlex: {e}")
            if hasattr(e.response, 'text'):
                print(f"Response: {e.response.text}")
            return None
    
    def search_works(
        self,
        query: str = None,
        filters: Dict[str, Any] = None,
        search_fields: List[str] = None,
        sort: str = None,
        page: int = 1,
        per_page: int = None
    ) -> Dict[str, Any]:
        """
        Search for works (papers) in OpenAlex
        
        Args:
            query: Free-text search query
            filters: Dictionary of filters (e.g., {'publication_year': 2023, 'is_oa': True})
            search_fields: Specific fields to search (default: title and abstract)
            sort: Sort order (e.g., 'cited_by_count:desc', 'publication_date:desc')
            page: Page number for pagination
            per_page: Results per page (1-200, defaults to instance setting if not provided)
        
        Returns:
            Response with 'results' (list of works) and 'meta' (pagination info)
        
        Example filters:
            - {'publication_year': 2023}
            - {'is_oa': True}
            - {'type': 'article'}
            - {'concepts.id': 'C41008148'}  # Computer Science
            - {'authorships.author.id': 'A5023888391'}  # Specific author
        """
        # Use provided per_page or fall back to instance default
        results_per_page = min(per_page or self.per_page, 200)
        
        params = {
            'per-page': results_per_page,
            'page': page
        }
        
        # Build search query
        if query:
            if search_fields:
                field_queries = [f"{field}.search:{query}" for field in search_fields]
                params['search'] = ','.join(field_queries)
            else:
                params['search'] = query
        
        # Add filters
        if filters:
            filter_parts = []
            for key, value in filters.items():
                if isinstance(value, bool):
                    filter_parts.append(f"{key}:{str(value).lower()}")
                elif isinstance(value, (list, tuple)):
                    filter_parts.append(f"{key}:{'|'.join(map(str, value))}")
                else:
                    filter_parts.append(f"{key}:{value}")
            params['filter'] = ','.join(filter_parts)
        
        # Add sorting
        if sort:
            params['sort'] = sort
        
        return self._make_request('works', params)
    
    def get_work(self, work_id: str) -> Dict[str, Any]:
        """
        Get a single work by OpenAlex ID, DOI, or PMID
        
        Args:
            work_id: OpenAlex ID (W...), DOI, or PMID
                    Examples: 'W2741809807', 'https://doi.org/10.7717/peerj.4375',
                             'https://openalex.org/W2741809807'
        """
        # Clean the ID
        if work_id.startswith('https://openalex.org/'):
            work_id = work_id.split('/')[-1]
        elif work_id.startswith('https://doi.org/'):
            work_id = f"doi:{work_id.split('doi.org/')[-1]}"
        
        return self._make_request(f'works/{work_id}')
    
    def get_author(self, author_id: str) -> Dict[str, Any]:
        """
        Get author details by OpenAlex ID or ORCID
        
        Args:
            author_id: OpenAlex ID (A...) or ORCID
        """
        if author_id.startswith('https://openalex.org/'):
            author_id = author_id.split('/')[-1]
        elif author_id.startswith('https://orcid.org/'):
            author_id = f"orcid:{author_id.split('orcid.org/')[-1]}"
        
        return self._make_request(f'authors/{author_id}')
    
    def get_venue(self, venue_id: str) -> Dict[str, Any]:
        """
        Get venue/source details by OpenAlex ID or ISSN
        
        Args:
            venue_id: OpenAlex ID (S...) or ISSN
        """
        if venue_id.startswith('https://openalex.org/'):
            venue_id = venue_id.split('/')[-1]
        elif '-' in venue_id and len(venue_id) == 9:  # ISSN format
            venue_id = f"issn:{venue_id}"
        
        return self._make_request(f'sources/{venue_id}')
    
    def get_related_works(self, work_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get works related to a given work
        
        Args:
            work_id: OpenAlex ID of the work
            limit: Number of related works to return
        """
        work = self.get_work(work_id)
        if not work or 'related_works' not in work:
            return []
        
        related_ids = work['related_works'][:limit]
        related_works = []
        
        for rel_id in related_ids:
            rel_work = self.get_work(rel_id)
            if rel_work:
                related_works.append(rel_work)
            time.sleep(0.1)  # Rate limiting
        
        return related_works
    
    @staticmethod
    def reconstruct_abstract(inverted_index: Dict[str, List[int]]) -> str:
        """
        Reconstruct abstract from OpenAlex's inverted index format
        
        Args:
            inverted_index: Dictionary mapping words to their positions
        
        Returns:
            Reconstructed abstract text
        """
        if not inverted_index:
            return ""
        
        word_positions = []
        for word, positions in inverted_index.items():
            for pos in positions:
                word_positions.append((pos, word))
        
        word_positions.sort()
        return ' '.join([word for pos, word in word_positions])
    
    def search_by_topic(
        self,
        topic_query: str,
        publication_year_start: Optional[int] = None,
        publication_year_end: Optional[int] = None,
        min_citations: Optional[int] = None,
        is_oa: Optional[bool] = None,
        page: int = 1,
        per_page: int = None
    ) -> Dict[str, Any]:
        """
        Search for works by topic with common filters
        
        Args:
            topic_query: Topic/keyword to search
            publication_year_start: Minimum publication year
            publication_year_end: Maximum publication year
            min_citations: Minimum citation count
            is_oa: Filter for open access papers
            page: Page number
            per_page: Results per page (1-200, defaults to instance setting if not provided)
        """
        filters = {}
        
        if publication_year_start and publication_year_end:
            filters['publication_year'] = f"{publication_year_start}-{publication_year_end}"
        elif publication_year_start:
            filters['from_publication_date'] = f"{publication_year_start}-01-01"
        elif publication_year_end:
            filters['to_publication_date'] = f"{publication_year_end}-12-31"
        
        if min_citations:
            filters['cited_by_count'] = f">{min_citations}"
        
        if is_oa is not None:
            filters['is_oa'] = is_oa
        
        return self.search_works(
            query=topic_query,
            filters=filters if filters else None,
            sort='cited_by_count:desc',
            page=page,
            per_page=per_page
        )


class EmbeddingGenerator:
    """Generate embeddings using a local sentence-transformers model."""
    
    def __init__(
        self,
        model: str = "sentence-transformers/all-MiniLM-L6-v2",
        cache_folder: str = "/tmp/.cache/huggingface",
    ):
        """Initialize the embedding model and cache location."""
        self.model = model
        self.cache_folder = cache_folder

        print(f"Loading embedding model {model}...")
        self._model = SentenceTransformer(model, cache_folder=cache_folder)
        self.dimension = self._model.get_sentence_embedding_dimension()
        print(f"✓ Model loaded successfully (dimension: {self.dimension})")
    
    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for a single text"""
        if not text or not text.strip():
            print("Warning: Empty text provided for embedding generation")
            return None

        try:
            embedding = self._model.encode(
                text.strip(),
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            print(f"✓ Generated embedding with {len(embedding)} dimensions")
            return embedding.tolist()
        except Exception as e:
            print(f"❌ Error generating embedding: {e}")
            print(f"   Text length: {len(text)} chars")
            return None
    
    def generate_embeddings_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """Generate embeddings for multiple texts in batch"""
        valid_texts = [t.strip() for t in texts if t and t.strip()]
        
        if not valid_texts:
            print("Warning: No valid texts provided for batch embedding generation")
            return []
        
        try:
            print(f"Generating embeddings for {len(valid_texts)} texts...")
            embeddings = []
            for i in range(0, len(valid_texts), batch_size):
                batch = valid_texts[i:i + batch_size]
                batch_embeddings = self._model.encode(
                    batch,
                    show_progress_bar=False,
                    normalize_embeddings=True,
                )
                embeddings.extend(batch_embeddings.tolist())
            print(f"✓ Successfully generated {len(embeddings)} embeddings")
            return embeddings
        except Exception as e:
            print(f"❌ Error generating batch embeddings: {e}")
            print(f"   Batch size: {len(valid_texts)}")
            return []


class AgentCapabilities:
    """Agent capabilities for the AI Research and Learning Copilot"""
    
    def __init__(self, embedding_generator: EmbeddingGenerator):
        self.embeddings = embedding_generator
    
    def find_papers_for_goal(self, goal_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Find papers matching a learning goal using semantic search"""
        query = """
        WITH goal AS (
            SELECT id, title, description, description_embedding
            FROM learning_goals
            WHERE id = %s
        )
        SELECT 
            p.id, p.title, p.abstract, p.doi, p.url, p.publication_date,
            p.venue, p.citation_count,
            1 - (p.abstract_embedding <=> g.description_embedding) AS similarity_score,
            ARRAY_AGG(
                DISTINCT jsonb_build_object(
                    'name', a.name,
                    'affiliation', a.affiliation,
                    'order', pa.author_order
                ) ORDER BY pa.author_order
            ) AS authors
        FROM papers p
        CROSS JOIN goal g
        LEFT JOIN paper_authors pa ON p.id = pa.paper_id
        LEFT JOIN authors a ON pa.author_id = a.id
        WHERE p.abstract_embedding IS NOT NULL
          AND g.description_embedding IS NOT NULL
        GROUP BY p.id, p.title, p.abstract, p.doi, p.url, p.publication_date,
                 p.venue, p.citation_count, p.abstract_embedding, g.description_embedding
        ORDER BY p.abstract_embedding <=> g.description_embedding ASC
        LIMIT %s
        """
        
        return lakebase.run_query(query, (goal_id, limit))
    
    def retrieve_evidence_from_collection(
        self, query: str, collection_id: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant evidence across papers in a collection"""
        # Generate query embedding
        query_embedding = self.embeddings.generate_embedding(query)
        
        sql = """
        SELECT 
            p.id AS paper_id, p.title, p.abstract, p.doi, p.url,
            1 - (p.abstract_embedding <=> %s::vector) AS relevance_score,
            cp.notes AS collection_notes, cp.added_at
        FROM collection_papers cp
        JOIN papers p ON cp.paper_id = p.id
        WHERE cp.collection_id = %s
          AND p.abstract_embedding IS NOT NULL
        ORDER BY p.abstract_embedding <=> %s::vector ASC
        LIMIT %s
        """
        
        return lakebase.run_query(sql, (query_embedding, collection_id, query_embedding, limit))
    
    def search_user_notes(self, query: str, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search through user's notes to find relevant past learning"""
        query_embedding = self.embeddings.generate_embedding(query)
        
        sql = """
        SELECT 
            n.id, n.content, n.highlight_text, n.page_number, n.created_at,
            1 - (n.content_embedding <=> %s::vector) AS relevance_score,
            p.id AS paper_id, p.title AS paper_title, p.doi,
            ARRAY_AGG(DISTINCT a.name ORDER BY pa.author_order) AS authors
        FROM notes n
        JOIN papers p ON n.paper_id = p.id
        LEFT JOIN paper_authors pa ON p.id = pa.paper_id
        LEFT JOIN authors a ON pa.author_id = a.id
        WHERE n.user_id = %s AND n.content_embedding IS NOT NULL
        GROUP BY n.id, n.content, n.highlight_text, n.page_number, n.created_at,
                 n.content_embedding, p.id, p.title, p.doi
        ORDER BY n.content_embedding <=> %s::vector ASC
        LIMIT %s
        """
        
        return lakebase.run_query(sql, (query_embedding, user_id, query_embedding, limit))
    
    def generate_reading_plan(
        self, goal_id: str, user_id: str
    ) -> List[Dict[str, Any]]:
        """Generate a sequenced reading plan for a learning goal"""
        query = """
        WITH goal AS (
            SELECT id, description_embedding
            FROM learning_goals WHERE id = %s
        ),
        relevant_papers AS (
            SELECT 
                p.id, p.title, p.abstract, p.publication_date, p.citation_count,
                1 - (p.abstract_embedding <=> g.description_embedding) AS relevance_score
            FROM papers p
            CROSS JOIN goal g
            WHERE p.abstract_embedding IS NOT NULL
              AND g.description_embedding IS NOT NULL
            ORDER BY p.abstract_embedding <=> g.description_embedding ASC
            LIMIT 50
        ),
        with_progress AS (
            SELECT 
                rp.*,
                COALESCE(prog.status, 'not_started') AS reading_status,
                COALESCE(prog.progress_percentage, 0) AS progress_percentage
            FROM relevant_papers rp
            LEFT JOIN reading_progress prog 
                ON rp.id = prog.paper_id AND prog.user_id = %s
        )
        SELECT 
            id AS paper_id, title, abstract, publication_date, citation_count,
            relevance_score, reading_status, progress_percentage,
            ROW_NUMBER() OVER (
                ORDER BY 
                    CASE reading_status 
                        WHEN 'not_started' THEN 1
                        WHEN 'in_progress' THEN 2
                        WHEN 'completed' THEN 3
                    END,
                    relevance_score DESC,
                    citation_count DESC
            ) AS reading_order,
            CASE 
                WHEN citation_count > 1000 THEN 'foundational'
                WHEN citation_count > 100 THEN 'intermediate'
                ELSE 'advanced/recent'
            END AS difficulty_estimate
        FROM with_progress
        ORDER BY reading_order
        """
        
        return lakebase.run_query(query, (goal_id, user_id))
    
    def recommend_next_paper(
        self, user_id: str, goal_id: str
    ) -> Optional[Dict[str, Any]]:
        """Recommend the next paper to read based on learning goal and progress"""
        query = """
        WITH goal AS (
            SELECT description_embedding
            FROM learning_goals WHERE id = %s
        ),
        completed_papers AS (
            SELECT paper_id FROM reading_progress
            WHERE user_id = %s AND status = 'completed'
        ),
        collection_papers AS (
            SELECT DISTINCT cp.paper_id
            FROM collections c
            JOIN collection_papers cp ON c.id = cp.collection_id
            WHERE c.user_id = %s
        )
        SELECT 
            p.id AS paper_id, p.title, p.abstract, p.doi, p.url,
            p.publication_date, p.citation_count,
            1 - (p.abstract_embedding <=> g.description_embedding) AS relevance_score,
            CASE WHEN p.id IN (SELECT paper_id FROM collection_papers) 
                 THEN true ELSE false END AS in_collection,
            ARRAY_AGG(DISTINCT a.name ORDER BY pa.author_order) AS authors
        FROM papers p
        CROSS JOIN goal g
        LEFT JOIN paper_authors pa ON p.id = pa.paper_id
        LEFT JOIN authors a ON pa.author_id = a.id
        WHERE p.abstract_embedding IS NOT NULL
          AND p.id NOT IN (SELECT paper_id FROM completed_papers)
        GROUP BY p.id, p.title, p.abstract, p.doi, p.url, p.publication_date,
                 p.citation_count, p.abstract_embedding, g.description_embedding
        ORDER BY in_collection DESC,
                 p.abstract_embedding <=> g.description_embedding ASC
        LIMIT 1
        """
        
        results = lakebase.run_query(query, (goal_id, user_id, user_id))
        return results[0] if results else None
    
    def hybrid_search(
        self, query_text: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Hybrid search combining keyword and semantic search"""
        query_embedding = self.embeddings.generate_embedding(query_text)
        
        sql = """
        WITH keyword_results AS (
            SELECT 
                id, title, abstract,
                ts_rank(
                    to_tsvector('english', title || ' ' || COALESCE(abstract, '')),
                    plainto_tsquery('english', %s)
                ) AS keyword_score
            FROM papers
            WHERE to_tsvector('english', title || ' ' || COALESCE(abstract, '')) @@ 
                  plainto_tsquery('english', %s)
        ),
        semantic_results AS (
            SELECT 
                id, title, abstract,
                1 - (abstract_embedding <=> %s::vector) AS semantic_score
            FROM papers
            WHERE abstract_embedding IS NOT NULL
            ORDER BY abstract_embedding <=> %s::vector ASC
            LIMIT 100
        )
        SELECT 
            COALESCE(k.id, s.id) AS paper_id,
            COALESCE(k.title, s.title) AS title,
            COALESCE(k.abstract, s.abstract) AS abstract,
            COALESCE(k.keyword_score, 0) * 0.3 + 
                COALESCE(s.semantic_score, 0) * 0.7 AS combined_score,
            COALESCE(k.keyword_score, 0) AS keyword_score,
            COALESCE(s.semantic_score, 0) AS semantic_score
        FROM keyword_results k
        FULL OUTER JOIN semantic_results s ON k.id = s.id
        ORDER BY combined_score DESC
        LIMIT %s
        """
        
        return lakebase.run_query(
            sql,
            (query_text, query_text, query_embedding, query_embedding, limit)
        )


class PaperIngestion:
    """Handle ingestion of new papers with embedding generation"""
    
    def __init__(self, embedding_generator: EmbeddingGenerator):
        self.embeddings = embedding_generator
    
    def ingest_paper(
        self,
        title: str,
        abstract: str,
        doi: Optional[str] = None,
        url: Optional[str] = None,
        publication_date: Optional[str] = None,
        venue: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        citation_count: int = 0,
        authors: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Ingest a new paper with automatic embedding generation"""
        # Generate abstract embedding
        abstract_embedding = self.embeddings.generate_embedding(abstract)
        
        with lakebase.get_connection() as conn:
            with conn.cursor() as cursor:
                # Insert paper
                cursor.execute(
                    """
                    INSERT INTO papers (
                        title, abstract, doi, url, publication_date, venue,
                        keywords, citation_count, abstract_embedding,
                        abstract_embedding_model, abstract_embedding_generated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    RETURNING id
                    """,
                    (
                        title, abstract, doi, url, publication_date, venue,
                        keywords, citation_count, abstract_embedding,
                        self.embeddings.model
                    )
                )
                result = cursor.fetchone()
                paper_id = result['id']
                
                # Insert authors if provided
                if authors:
                    for idx, author_info in enumerate(authors, start=1):
                        # Check if author exists
                        cursor.execute(
                            "SELECT id FROM authors WHERE name = %s",
                            (author_info['name'],)
                        )
                        author_result = cursor.fetchone()
                        
                        if author_result:
                            author_id = author_result['id']
                        else:
                            # Create new author
                            cursor.execute(
                                """
                                INSERT INTO authors (name, affiliation, h_index)
                                VALUES (%s, %s, %s)
                                RETURNING id
                                """,
                                (
                                    author_info['name'],
                                    author_info.get('affiliation'),
                                    author_info.get('h_index')
                                )
                            )
                            author_id = cursor.fetchone()['id']
                        
                        # Link paper to author
                        cursor.execute(
                            """
                            INSERT INTO paper_authors (paper_id, author_id, author_order)
                            VALUES (%s, %s, %s)
                            """,
                            (paper_id, author_id, idx)
                        )
                
                conn.commit()
        
        return paper_id
    
    def update_paper_embeddings(self, paper_ids: Optional[List[str]] = None):
        """Batch update embeddings for papers"""
        if paper_ids:
            sql = """
                SELECT id, abstract FROM papers
                WHERE id = ANY(%s) AND abstract IS NOT NULL
                  AND abstract_embedding IS NULL
                """
            papers = lakebase.run_query(sql, (paper_ids,))
        else:
            sql = """
                SELECT id, abstract FROM papers
                WHERE abstract IS NOT NULL AND abstract_embedding IS NULL
                LIMIT 100
                """
            papers = lakebase.run_query(sql)
        
        if not papers:
            return 0
        
        # Generate embeddings in batch
        abstracts = [p['abstract'] for p in papers]
        embeddings = self.embeddings.generate_embeddings_batch(abstracts)
        
        # Update database
        with lakebase.get_connection() as conn:
            with conn.cursor() as cursor:
                for paper, embedding in zip(papers, embeddings):
                    cursor.execute(
                        """
                        UPDATE papers
                        SET abstract_embedding = %s,
                            abstract_embedding_model = %s,
                            abstract_embedding_generated_at = NOW()
                        WHERE id = %s
                        """,
                        (embedding, self.embeddings.model, paper['id'])
                    )
                
                conn.commit()
        
        return len(papers)
    
    @classmethod
    def ingest_from_openalex(
        cls,
        openalex_work: Dict[str, Any],
        upsert: bool = False,
        generate_chunks: bool = True
    ) -> str:
        """
        Ingest a paper from OpenAlex API response
        
        Args:
            openalex_work: Work object from OpenAlex API
            upsert: If True, update existing papers instead of failing on conflict
            generate_chunks: Whether to generate text chunks (default: True)
        
        Returns:
            paper_id of the ingested paper
        """
        import json
        from databricks.sdk import WorkspaceClient
        
        # Create embedding generator
        w = WorkspaceClient()
        databricks_host = w.config.host
        databricks_token = w.config.token.value if hasattr(w.config.token, 'value') else str(w.config.token)
        
        embedding_generator = EmbeddingGenerator()
        
        # Extract OpenAlex ID
        openalex_id = openalex_work.get('id', '')
        if openalex_id.startswith('https://openalex.org/'):
            openalex_id = openalex_id.split('/')[-1]
        
        # Extract DOI
        doi = openalex_work.get('doi', '')
        if doi and doi.startswith('https://doi.org/'):
            doi = doi.replace('https://doi.org/', '')
        
        # Reconstruct abstract
        abstract = ""
        abstract_inverted_index = openalex_work.get('abstract_inverted_index')
        if abstract_inverted_index:
            abstract = OpenAlexClient.reconstruct_abstract(abstract_inverted_index)
        
        # Extract venue information
        primary_location = openalex_work.get('primary_location', {})
        source = primary_location.get('source', {})
        
        venue_id = None
        venue_display_name = source.get('display_name') if source else None
        
        # Insert venue if it doesn't exist
        if source and source.get('id'):
            venue_openalex_id = source['id']
            if venue_openalex_id.startswith('https://openalex.org/'):
                venue_openalex_id = venue_openalex_id.split('/')[-1]

            try:
                with lakebase.get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "SELECT id FROM venues WHERE openalex_id = %s",
                            (venue_openalex_id,)
                        )
                        venue_row = cursor.fetchone()

                        if venue_row:
                            venue_id = venue_row['id']
                        else:
                            cursor.execute(
                                """
                                INSERT INTO venues (
                                    openalex_id, display_name, issn_l, issn, is_oa, type, host_organization
                                )
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                                RETURNING id
                                """,
                                (
                                    venue_openalex_id,
                                    source.get('display_name'),
                                    source.get('issn_l'),
                                    json.dumps(source.get('issn', [])),
                                    source.get('is_oa', False),
                                    source.get('type'),
                                    source.get('host_organization_name')
                                )
                            )
                            venue_result = cursor.fetchone()
                            conn.commit()
                            venue_id = venue_result['id'] if venue_result else None
            except Exception as venue_error:
                print(f"⚠ Could not resolve venue {venue_openalex_id}: {venue_error}")
                venue_id = None
        
        # Generate abstract embedding
        abstract_embedding = None
        if abstract:
            print(f"Generating embedding for paper: {openalex_work.get('title', 'Unknown')[:60]}...")
            print(f"   Abstract length: {len(abstract)} chars")
            abstract_embedding = embedding_generator.generate_embedding(abstract)
            if abstract_embedding:
                print(f"   ✓ Abstract embedding generated successfully")
            else:
                print(f"   ❌ Failed to generate abstract embedding")
        else:
            print(f"   ⚠ No abstract available for paper: {openalex_work.get('title', 'Unknown')[:60]}...")
        
        # Extract OpenAccess info
        open_access = openalex_work.get('open_access', {})
        
        # Build SQL query with optional upsert
        upsert_clause = ""
        if upsert:
            upsert_clause = """
                    ON CONFLICT (openalex_id) DO UPDATE
                    SET 
                        doi = EXCLUDED.doi,
                        title = EXCLUDED.title,
                        display_name = EXCLUDED.display_name,
                        cited_by_count = EXCLUDED.cited_by_count,
                        updated_at = NOW()
            """
        
        # Insert paper
        with lakebase.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO papers (
                        openalex_id, doi, title, display_name, abstract, abstract_inverted_index,
                        publication_year, publication_date, type, language,
                        venue_id, venue_display_name,
                        is_oa, oa_status, pdf_url, landing_page_url,
                        cited_by_count, biblio, keywords, concepts, topics, mesh,
                        sustainable_development_goals, referenced_works_count, referenced_works,
                        is_retracted, is_paratext,
                        abstract_embedding, abstract_embedding_model, abstract_embedding_generated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                    )
                    {upsert_clause}
                    RETURNING id
                    """,
                    (
                        openalex_id,
                        doi,
                        openalex_work.get('title', openalex_work.get('display_name')),
                        openalex_work.get('display_name'),
                        abstract,
                        json.dumps(abstract_inverted_index) if abstract_inverted_index else None,
                        openalex_work.get('publication_year'),
                        openalex_work.get('publication_date'),
                        openalex_work.get('type'),
                        openalex_work.get('language'),
                        venue_id,
                        venue_display_name,
                        open_access.get('is_oa', False),
                        open_access.get('oa_status'),
                        primary_location.get('pdf_url'),
                        primary_location.get('landing_page_url'),
                        openalex_work.get('cited_by_count', 0),
                        json.dumps(openalex_work.get('biblio', {})),
                        json.dumps(openalex_work.get('keywords', [])),
                        json.dumps(openalex_work.get('concepts', [])),
                        json.dumps(openalex_work.get('topics', [])),
                        json.dumps(openalex_work.get('mesh', [])),
                        json.dumps(openalex_work.get('sustainable_development_goals', [])),
                        openalex_work.get('referenced_works_count', 0),
                        json.dumps(openalex_work.get('referenced_works', [])),
                        openalex_work.get('is_retracted', False),
                        openalex_work.get('is_paratext', False),
                        abstract_embedding,
                        embedding_generator.model if abstract_embedding else None
                    )
                )
                
                result = cursor.fetchone()
                paper_id = result['id']
                
                # Insert authors
                authorships = openalex_work.get('authorships', [])
                for authorship in authorships:
                    author_data = authorship.get('author', {})
                    author_openalex_id = author_data.get('id', '')
                    
                    if author_openalex_id.startswith('https://openalex.org/'):
                        author_openalex_id = author_openalex_id.split('/')[-1]
                    
                    if not author_openalex_id:
                        continue
                    
                    # Check if author exists
                    cursor.execute(
                        "SELECT id FROM authors WHERE openalex_id = %s",
                        (author_openalex_id,)
                    )
                    author_result = cursor.fetchone()
                    
                    if author_result:
                        author_id = author_result['id']
                    else:
                        # Create new author
                        cursor.execute(
                            """
                            INSERT INTO authors (
                                openalex_id, display_name, orcid
                            )
                            VALUES (%s, %s, %s)
                            RETURNING id
                            """,
                            (
                                author_openalex_id,
                                author_data.get('display_name'),
                                author_data.get('orcid')
                            )
                        )
                        author_id = cursor.fetchone()['id']
                    
                    # Link paper to author with authorship details
                    cursor.execute(
                        """
                        INSERT INTO paper_authors (
                            paper_id, author_id, author_order, author_position,
                            is_corresponding, raw_author_name, raw_affiliation_strings,
                            institutions, countries
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (paper_id, author_id) DO NOTHING
                        """,
                        (
                            paper_id,
                            author_id,
                            len([a for a in authorships if a.get('author', {}).get('id', '').endswith(author_openalex_id)]),
                            authorship.get('author_position'),
                            authorship.get('is_corresponding', False),
                            authorship.get('raw_author_name'),
                            json.dumps(authorship.get('raw_affiliation_strings', [])),
                            json.dumps(authorship.get('institutions', [])),
                            json.dumps(authorship.get('countries', []))
                        )
                    )
                
                conn.commit()
        
        # Generate chunks if requested
        if generate_chunks and abstract:
            print(f"Creating chunks for paper {paper_id}...")
            chunk_manager = ChunkManager(embedding_generator)
            chunks_created = chunk_manager.create_paper_chunks(
                paper_id=paper_id,
                full_text=abstract,
                chunking_method='paragraphs',
                generate_embeddings=True
            )
            print(f"   ✓ Created {chunks_created} chunks with embeddings")
        elif generate_chunks and not abstract:
            print(f"   ⚠ Skipping chunk generation: no abstract available")
        
        return paper_id


class TextChunker:
    """Utilities for chunking text into semantic segments"""
    
    @staticmethod
    def chunk_by_paragraphs(
        text: str,
        max_chunk_size: int = 512,
        overlap: int = 50
    ) -> List[Dict[str, Any]]:
        """Split text into chunks by paragraphs with overlap
        
        Args:
            text: Full text to chunk
            max_chunk_size: Maximum characters per chunk
            overlap: Number of characters to overlap between chunks
        
        Returns:
            List of chunk dictionaries with text, start_char, end_char, word_count
        """
        # Split by double newlines (paragraphs) or single newlines
        paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        
        chunks = []
        current_chunk = ""
        start_char = 0
        chunk_index = 0
        
        for para in paragraphs:
            # If adding this paragraph exceeds max size, save current chunk
            if current_chunk and len(current_chunk) + len(para) > max_chunk_size:
                chunk_text = current_chunk.strip()
                chunks.append({
                    'chunk_index': chunk_index,
                    'chunk_text': chunk_text,
                    'start_char': start_char,
                    'end_char': start_char + len(chunk_text),
                    'word_count': len(chunk_text.split())
                })
                
                # Start new chunk with overlap
                overlap_text = chunk_text[-overlap:] if len(chunk_text) > overlap else chunk_text
                current_chunk = overlap_text + " " + para
                start_char = start_char + len(chunk_text) - len(overlap_text)
                chunk_index += 1
            else:
                # Add paragraph to current chunk
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para
        
        # Add final chunk
        if current_chunk:
            chunk_text = current_chunk.strip()
            chunks.append({
                'chunk_index': chunk_index,
                'chunk_text': chunk_text,
                'start_char': start_char,
                'end_char': start_char + len(chunk_text),
                'word_count': len(chunk_text.split())
            })
        
        return chunks
    
    @staticmethod
    def chunk_by_sections(
        text: str,
        section_headers: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Split text by section headers (e.g., Abstract, Introduction, Methods)
        
        Args:
            text: Full text to chunk
            section_headers: List of expected section headers. If None, uses common patterns.
        
        Returns:
            List of chunk dictionaries with section information
        """
        if section_headers is None:
            section_headers = [
                'abstract', 'introduction', 'background', 'related work',
                'methods', 'methodology', 'approach', 'results', 'experiments',
                'discussion', 'conclusion', 'future work', 'references'
            ]
        
        # Create regex pattern to match section headers
        pattern = r'\n\s*(' + '|'.join(
            [re.escape(h) for h in section_headers]
        ) + r')\s*\n'
        
        sections = re.split(pattern, text, flags=re.IGNORECASE)
        
        chunks = []
        chunk_index = 0
        start_char = 0
        
        # Process sections (alternates between header and content)
        for i in range(0, len(sections) - 1, 2):
            section_title = sections[i+1].strip() if i+1 < len(sections) else None
            section_content = sections[i+2].strip() if i+2 < len(sections) else sections[i].strip()
            
            if section_content:
                # Determine chunk type from section title
                chunk_type = 'paragraph'
                if section_title:
                    title_lower = section_title.lower()
                    if 'abstract' in title_lower:
                        chunk_type = 'abstract'
                    elif 'intro' in title_lower:
                        chunk_type = 'introduction'
                    elif 'method' in title_lower or 'approach' in title_lower:
                        chunk_type = 'methods'
                    elif 'result' in title_lower or 'experiment' in title_lower:
                        chunk_type = 'results'
                    elif 'discuss' in title_lower:
                        chunk_type = 'discussion'
                    elif 'conclu' in title_lower:
                        chunk_type = 'conclusion'
                    else:
                        chunk_type = 'section'
                
                chunks.append({
                    'chunk_index': chunk_index,
                    'chunk_text': section_content,
                    'chunk_type': chunk_type,
                    'section_title': section_title,
                    'start_char': start_char,
                    'end_char': start_char + len(section_content),
                    'word_count': len(section_content.split())
                })
                
                start_char += len(section_content)
                chunk_index += 1
        
        return chunks


class ChunkManager:
    """Manage paper chunks with embeddings for granular search"""
    
    def __init__(self, embedding_generator: EmbeddingGenerator):
        self.embeddings = embedding_generator
        self.chunker = TextChunker()
    
    def create_paper_chunks(
        self,
        paper_id: str,
        full_text: str,
        chunking_method: str = 'paragraphs',
        max_chunk_size: int = 512,
        generate_embeddings: bool = True
    ) -> int:
        """Create and store chunks for a paper
        
        Args:
            paper_id: ID of the paper
            full_text: Full text content of the paper
            chunking_method: 'paragraphs' or 'sections'
            max_chunk_size: Maximum characters per chunk (for paragraph method)
            generate_embeddings: Whether to generate embeddings immediately
        
        Returns:
            Number of chunks created
        """
        # Generate chunks
        if chunking_method == 'sections':
            chunks = self.chunker.chunk_by_sections(full_text)
        else:
            chunks = self.chunker.chunk_by_paragraphs(full_text, max_chunk_size)
        
        if not chunks:
            print(f"   ⚠ No chunks created from text (length: {len(full_text)})")
            return 0
        
        print(f"   Created {len(chunks)} chunks from text")
        
        # Generate embeddings if requested
        embeddings = None
        if generate_embeddings:
            chunk_texts = [c['chunk_text'] for c in chunks]
            embeddings = self.embeddings.generate_embeddings_batch(chunk_texts)
            if not embeddings:
                print(f"   ❌ Failed to generate embeddings for chunks")
            elif len(embeddings) != len(chunks):
                print(f"   ⚠ Embedding count mismatch: {len(embeddings)} embeddings for {len(chunks)} chunks")
        
        # Insert chunks into database
        with lakebase.get_connection() as conn:
            with conn.cursor() as cursor:
                for idx, chunk in enumerate(chunks):
                    embedding = embeddings[idx] if embeddings else None
                    
                    cursor.execute(
                        """
                        INSERT INTO paper_chunks (
                            paper_id, chunk_index, chunk_text, chunk_type,
                            section_title, start_char, end_char, word_count,
                            chunk_embedding, embedding_model, embedding_generated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (paper_id, chunk_index) DO UPDATE
                        SET chunk_text = EXCLUDED.chunk_text,
                            chunk_embedding = EXCLUDED.chunk_embedding,
                            embedding_model = EXCLUDED.embedding_model,
                            embedding_generated_at = NOW(),
                            updated_at = NOW()
                        """,
                        (
                            paper_id,
                            chunk['chunk_index'],
                            chunk['chunk_text'],
                            chunk.get('chunk_type', 'paragraph'),
                            chunk.get('section_title'),
                            chunk['start_char'],
                            chunk['end_char'],
                            chunk['word_count'],
                            embedding,
                            self.embeddings.model if embedding else None
                        )
                    )
                
                conn.commit()
        
        return len(chunks)
    
    def search_chunks(
        self,
        query: str,
        limit: int = 10,
        paper_ids: Optional[List[str]] = None,
        chunk_types: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Search for relevant chunks using semantic similarity
        
        Args:
            query: Search query
            limit: Maximum number of chunks to return
            paper_ids: Optional list of paper IDs to restrict search
            chunk_types: Optional list of chunk types to filter (e.g., ['results', 'discussion'])
        
        Returns:
            List of relevant chunks with metadata
        """
        query_embedding = self.embeddings.generate_embedding(query)
        
        # Build WHERE clause
        where_conditions = ["pc.chunk_embedding IS NOT NULL"]
        params = [query_embedding, query_embedding]
        
        if paper_ids:
            where_conditions.append("pc.paper_id = ANY(%s)")
            params.append(paper_ids)
        
        if chunk_types:
            where_conditions.append("pc.chunk_type = ANY(%s)")
            params.append(chunk_types)
        
        where_clause = " AND " + " AND ".join(where_conditions)
        
        sql = f"""
        SELECT 
            pc.id AS chunk_id,
            pc.paper_id,
            pc.chunk_index,
            pc.chunk_text,
            pc.chunk_type,
            pc.section_title,
            pc.page_number,
            pc.word_count,
            1 - (pc.chunk_embedding <=> %s::vector) AS relevance_score,
            p.title AS paper_title,
            p.doi,
            p.url,
            p.publication_date,
            p.citation_count,
            ARRAY_AGG(
                DISTINCT jsonb_build_object(
                    'name', a.name,
                    'affiliation', a.affiliation
                ) ORDER BY pa.author_order
            ) AS authors
        FROM paper_chunks pc
        JOIN papers p ON pc.paper_id = p.id
        LEFT JOIN paper_authors pa ON p.id = pa.paper_id
        LEFT JOIN authors a ON pa.author_id = a.id
        {where_clause}
        GROUP BY pc.id, pc.paper_id, pc.chunk_index, pc.chunk_text, pc.chunk_type,
                 pc.section_title, pc.page_number, pc.word_count, pc.chunk_embedding,
                 p.title, p.doi, p.url, p.publication_date, p.citation_count
        ORDER BY pc.chunk_embedding <=> %s::vector ASC
        LIMIT %s
        """
        
        params.append(limit)
        
        return lakebase.run_query(sql, tuple(params))
    
    def get_chunk_context(
        self,
        chunk_id: str,
        context_size: int = 2
    ) -> Dict[str, Any]:
        """Get a chunk with surrounding context chunks
        
        Args:
            chunk_id: ID of the target chunk
            context_size: Number of chunks before and after to include
        
        Returns:
            Dictionary with target chunk and context chunks
        """
        sql = """
        WITH target AS (
            SELECT paper_id, chunk_index
            FROM paper_chunks
            WHERE id = %s
        )
        SELECT 
            pc.id AS chunk_id,
            pc.chunk_index,
            pc.chunk_text,
            pc.chunk_type,
            pc.section_title,
            CASE WHEN pc.id = %s THEN true ELSE false END AS is_target
        FROM paper_chunks pc
        JOIN target t ON pc.paper_id = t.paper_id
        WHERE pc.chunk_index BETWEEN t.chunk_index - %s AND t.chunk_index + %s
        ORDER BY pc.chunk_index
        """
        
        return lakebase.run_query(sql, (chunk_id, chunk_id, context_size, context_size))
    
    def get_paper_chunks(
        self,
        paper_id: str,
        chunk_types: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Get all chunks for a paper
        
        Args:
            paper_id: ID of the paper
            chunk_types: Optional filter by chunk types
        
        Returns:
            List of chunks ordered by chunk_index
        """
        where_clause = "WHERE paper_id = %s"
        params = [paper_id]
        
        if chunk_types:
            where_clause += " AND chunk_type = ANY(%s)"
            params.append(chunk_types)
        
        sql = f"""
        SELECT 
            id AS chunk_id,
            chunk_index,
            chunk_text,
            chunk_type,
            section_title,
            page_number,
            word_count
        FROM paper_chunks
        {where_clause}
        ORDER BY chunk_index
        """
        
        return lakebase.run_query(sql, tuple(params))
    
    def update_chunk_embeddings(
        self,
        paper_ids: Optional[List[str]] = None,
        batch_size: int = 50
    ) -> int:
        """Batch update embeddings for chunks missing them
        
        Args:
            paper_ids: Optional list of paper IDs to process
            batch_size: Number of chunks to process at once
        
        Returns:
            Number of chunks updated
        """
        where_clause = "WHERE chunk_embedding IS NULL"
        params = []
        
        if paper_ids:
            where_clause += " AND paper_id = ANY(%s)"
            params.append(paper_ids)
        
        sql = f"""
        SELECT id, chunk_text
        FROM paper_chunks
        {where_clause}
        LIMIT %s
        """
        params.append(batch_size)
        
        chunks = lakebase.run_query(sql, tuple(params))
        
        if not chunks:
            return 0
        
        # Generate embeddings
        chunk_texts = [c['chunk_text'] for c in chunks]
        embeddings = self.embeddings.generate_embeddings_batch(chunk_texts)
        
        # Update database
        with lakebase.get_connection() as conn:
            with conn.cursor() as cursor:
                for chunk, embedding in zip(chunks, embeddings):
                    if embedding:
                        cursor.execute(
                            """
                            UPDATE paper_chunks
                            SET chunk_embedding = %s,
                                embedding_model = %s,
                                embedding_generated_at = NOW(),
                                updated_at = NOW()
                            WHERE id = %s
                            """,
                            (embedding, self.embeddings.model, chunk['id'])
                        )
                
                conn.commit()
        
        return len(chunks)