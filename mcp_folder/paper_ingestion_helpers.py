"""Enhanced paper ingestion helpers with embeddings, chunking, and full metadata support.

This module provides advanced ingestion capabilities matching app.py functionality.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import requests
from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)

# Import base functions from the main helper module
from papers_mcp_helper_functions import get_connection, OpenAlexClient

# Cache embedding generator to avoid multiple initializations
_embedding_generator_cache = None


class EmbeddingGenerator:
    """Generate text embeddings using Databricks Foundation Model APIs."""
    
    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self.model = model
        self.w = WorkspaceClient()
        self.databricks_host = self.w.config.host
        self.databricks_token = (
            self.w.config.token.value if hasattr(self.w.config.token, "value") else str(self.w.config.token)
        )
        logger.info(f"EmbeddingGenerator initialized with model: {model}")
    
    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding for a single text."""
        if not text or not text.strip():
            return None
        
        result = self.generate_embeddings_batch([text])
        return result[0] if result else None
    
    def generate_embeddings_batch(self, texts: List[str]) -> Optional[List[List[float]]]:
        """Generate embeddings for a batch of texts."""
        if not texts:
            return None
        
        # Filter out empty texts
        valid_texts = [t for t in texts if t and t.strip()]
        if not valid_texts:
            return None
        
        try:
            endpoint = "api/2.0/vector-search/embeddings"
            url = f"{self.databricks_host}/{endpoint}"
            
            payload = {
                "texts": valid_texts,
                "model": self.model
            }
            
            headers = {
                "Authorization": f"Bearer {self.databricks_token}",
                "Content-Type": "application/json"
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            embeddings = result.get("embeddings")
            
            if embeddings and len(embeddings) == len(valid_texts):
                return embeddings
            else:
                logger.error(f"Embedding count mismatch: got {len(embeddings) if embeddings else 0}, expected {len(valid_texts)}")
                return None
        
        except Exception as e:
            logger.error(f"Error generating embeddings: {e}", exc_info=True)
            return None


class TextChunker:
    """Utilities for chunking text into semantic segments."""
    
    @staticmethod
    def chunk_by_paragraphs(
        text: str,
        max_chunk_size: int = 512,
        overlap: int = 50
    ) -> List[Dict[str, Any]]:
        """Split text into chunks by paragraphs with overlap."""
        paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        
        chunks = []
        current_chunk = ""
        start_char = 0
        chunk_index = 0
        
        for para in paragraphs:
            if current_chunk and len(current_chunk) + len(para) > max_chunk_size:
                chunk_text = current_chunk.strip()
                chunks.append({
                    'chunk_index': chunk_index,
                    'chunk_text': chunk_text,
                    'start_char': start_char,
                    'end_char': start_char + len(chunk_text),
                    'word_count': len(chunk_text.split())
                })
                
                overlap_text = chunk_text[-overlap:] if len(chunk_text) > overlap else chunk_text
                current_chunk = overlap_text + " " + para
                start_char = start_char + len(chunk_text) - len(overlap_text)
                chunk_index += 1
            else:
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para
        
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


class ChunkManager:
    """Manage paper chunks with embeddings for granular search."""
    
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
        """Create and store chunks for a paper."""
        chunks = self.chunker.chunk_by_paragraphs(full_text, max_chunk_size)
        
        if not chunks:
            logger.warning(f"No chunks created from text (length: {len(full_text)})")
            return 0
        
        logger.info(f"Created {len(chunks)} chunks from text")
        
        embeddings = None
        if generate_embeddings:
            chunk_texts = [c['chunk_text'] for c in chunks]
            embeddings = self.embeddings.generate_embeddings_batch(chunk_texts)
            if not embeddings:
                logger.error("Failed to generate embeddings for chunks")
            elif len(embeddings) != len(chunks):
                logger.warning(f"Embedding count mismatch: {len(embeddings)} embeddings for {len(chunks)} chunks")
        
        with get_connection() as conn:
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


class PaperIngestion:
    """Enhanced paper ingestion with full metadata, embeddings, and relationships."""
    
    @staticmethod
    def get_embedding_generator() -> EmbeddingGenerator:
        """Get or create a cached embedding generator."""
        global _embedding_generator_cache
        if _embedding_generator_cache is None:
            _embedding_generator_cache = EmbeddingGenerator()
        return _embedding_generator_cache
    
    @classmethod
    def ingest_from_openalex(
        cls,
        openalex_work: Dict[str, Any],
        upsert: bool = False,
        generate_chunks: bool = True
    ) -> Optional[str]:
        """Ingest a paper from OpenAlex with full metadata, embeddings, venues, authors, and chunks."""
        if not openalex_work:
            return None
        
        embedding_generator = cls.get_embedding_generator()
        
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
                with get_connection() as conn:
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
                logger.error(f"Could not resolve venue {venue_openalex_id}: {venue_error}")
                venue_id = None
        
        # Generate abstract embedding
        abstract_embedding = None
        if abstract:
            logger.info(f"Generating embedding for paper: {openalex_work.get('title', 'Unknown')[:60]}...")
            abstract_embedding = embedding_generator.generate_embedding(abstract)
            if abstract_embedding:
                logger.info("✓ Abstract embedding generated")
            else:
                logger.warning("✗ Failed to generate abstract embedding")
        
        # Extract OpenAccess info
        open_access = openalex_work.get('open_access', {})
        
        # Build upsert clause
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
        
        # Insert paper with full metadata
        with get_connection() as conn:
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
                        openalex_id, doi,
                        openalex_work.get('title', openalex_work.get('display_name')),
                        openalex_work.get('display_name'),
                        abstract,
                        json.dumps(abstract_inverted_index) if abstract_inverted_index else None,
                        openalex_work.get('publication_year'),
                        openalex_work.get('publication_date'),
                        openalex_work.get('type'),
                        openalex_work.get('language'),
                        venue_id, venue_display_name,
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
                for authorship in openalex_work.get('authorships', []):
                    author_data = authorship.get('author', {})
                    author_openalex_id = author_data.get('id', '')
                    
                    if author_openalex_id.startswith('https://openalex.org/'):
                        author_openalex_id = author_openalex_id.split('/')[-1]
                    
                    if not author_openalex_id:
                        continue
                    
                    cursor.execute(
                        "SELECT id FROM authors WHERE openalex_id = %s",
                        (author_openalex_id,)
                    )
                    author_result = cursor.fetchone()
                    
                    if author_result:
                        author_id = author_result['id']
                    else:
                        cursor.execute(
                            """
                            INSERT INTO authors (openalex_id, display_name, orcid)
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
                            paper_id, author_id,
                            len([a for a in openalex_work.get('authorships', []) if a.get('author', {}).get('id', '').endswith(author_openalex_id)]),
                            authorship.get('author_position'),
                            authorship.get('is_corresponding', False),
                            authorship.get('raw_author_name'),
                            json.dumps(authorship.get('raw_affiliation_strings', [])),
                            json.dumps(authorship.get('institutions', [])),
                            json.dumps(authorship.get('countries', []))
                        )
                    )
                
                conn.commit()
        
        # Generate chunks
        if generate_chunks and abstract:
            logger.info(f"Creating chunks for paper {paper_id}...")
            chunk_manager = ChunkManager(embedding_generator)
            chunks_created = chunk_manager.create_paper_chunks(
                paper_id=paper_id,
                full_text=abstract,
                chunking_method='paragraphs',
                generate_embeddings=True
            )
            logger.info(f"✓ Created {chunks_created} chunks with embeddings")
        
        return paper_id