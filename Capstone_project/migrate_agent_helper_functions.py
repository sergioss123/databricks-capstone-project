#!/usr/bin/env python3
"""
Automated migration script for agent_helper_functions.py
Migrates from Databricks Foundation Model API to sentence-transformers

Usage:
    python migrate_agent_helper_functions.py
"""

import re
import shutil
from datetime import datetime

def backup_file(filepath):
    """Create a backup of the original file"""
    backup_path = f"{filepath}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(filepath, backup_path)
    print(f"✅ Backup created: {backup_path}")
    return backup_path

def read_file(filepath):
    """Read the file content"""
    with open(filepath, 'r') as f:
        return f.read()

def write_file(filepath, content):
    """Write content to file"""
    with open(filepath, 'w') as f:
        f.write(content)

def add_import(content):
    """Add sentence-transformers import after existing imports"""
    # Find the last import line before the first class
    pattern = r'(from databricks\.sdk import WorkspaceClient\n\n)'
    replacement = r'\1from sentence_transformers import SentenceTransformer\n\n'
    
    if 'from sentence_transformers import SentenceTransformer' in content:
        print("⚠️  sentence-transformers import already exists")
        return content
    
    content = re.sub(pattern, replacement, content)
    print("✅ Added sentence-transformers import")
    return content

def replace_embedding_generator(content):
    """Replace the EmbeddingGenerator class"""
    
    new_class = '''class EmbeddingGenerator:
    """Generate embeddings using sentence-transformers models (local)
    
    This replaces the Databricks Foundation Model API approach with
    local sentence-transformers models for better control and cost efficiency.
    Default model is all-MiniLM-L6-v2 (384 dimensions).
    """
    
    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2", 
                 cache_folder: str = "/tmp/.cache/huggingface"):
        """
        Initialize sentence-transformers embedding generator
        
        Args:
            model: Sentence-transformers model name (default: sentence-transformers/all-MiniLM-L6-v2)
            cache_folder: Directory to cache downloaded models
        """
        import os
        from sentence_transformers import SentenceTransformer
        
        # Set up HuggingFace cache environment variables
        os.environ["HF_HOME"] = cache_folder
        os.environ["TRANSFORMERS_CACHE"] = cache_folder
        os.environ["HF_HUB_CACHE"] = cache_folder
        
        self.model_name = model
        self.cache_folder = cache_folder
        
        # Load the model once
        print(f"Loading embedding model {model}...")
        self._model = SentenceTransformer(model, cache_folder=cache_folder)
        
        # Get dimension from the model
        self.dimension = self._model.get_sentence_embedding_dimension()
        print(f"✓ Model loaded successfully (dimension: {self.dimension})")
        
        # For backward compatibility
        self.model = model
    
    def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for a single text"""
        if not text or not text.strip():
            print("Warning: Empty text provided for embedding generation")
            return None
        
        try:
            embedding = self._model.encode(text.strip(), show_progress_bar=False)
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
            all_embeddings = []
            for i in range(0, len(valid_texts), batch_size):
                batch = valid_texts[i:i+batch_size]
                embeddings = self._model.encode(batch, show_progress_bar=False)
                all_embeddings.extend(embeddings.tolist())
                if (i + batch_size) % 128 == 0:
                    print(f"  Processed {min(i + batch_size, len(valid_texts))}/{len(valid_texts)} texts")
            print(f"✓ Successfully generated {len(all_embeddings)} embeddings")
            return all_embeddings
        except Exception as e:
            print(f"❌ Error generating batch embeddings: {e}")
            return []
'''
    
    # Pattern to match the entire EmbeddingGenerator class
    pattern = r'class EmbeddingGenerator:.*?(?=\nclass [A-Z]|\Z)'
    
    content = re.sub(pattern, new_class, content, flags=re.DOTALL)
    print("✅ Replaced EmbeddingGenerator class")
    return content

def add_overlapping_chunks_method(content):
    """Add the chunk_by_overlapping_windows method to TextChunker"""
    
    new_method = '''    @staticmethod
    def chunk_by_overlapping_windows(
        text: str,
        chunk_size: int = 512,
        overlap: int = 50
    ) -> List[Dict[str, Any]]:
        """Split text into overlapping chunks (sliding window approach)
        
        Based on implementation from ingest_ticker_news_embeddings notebook.
        Creates overlapping chunks by sliding a fixed-size window across text.
        
        Args:
            text: Full text to chunk
            chunk_size: Maximum characters per chunk (default: 512)
            overlap: Number of characters to overlap between chunks (default: 50)
        
        Returns:
            List of chunk dictionaries with chunk_index, chunk_text, start_char, end_char, word_count
        """
        if not text or not text.strip():
            return []
        
        chunks = []
        chunk_index = 0
        
        for start in range(0, len(text), chunk_size - overlap):
            chunk_text = text[start : start + chunk_size].strip()
            
            if not chunk_text:
                continue
            
            chunks.append({
                'chunk_index': chunk_index,
                'chunk_text': chunk_text,
                'start_char': start,
                'end_char': start + len(chunk_text),
                'word_count': len(chunk_text.split())
            })
            
            chunk_index += 1
            
            if start + chunk_size >= len(text):
                break
        
        return chunks
    
'''
    
    # Find TextChunker class and add method before chunk_by_paragraphs
    pattern = r'(class TextChunker:.*?"""\n\n)(\s+@staticmethod\n\s+def chunk_by_paragraphs)'
    replacement = r'\1' + new_method + r'\2'
    
    if 'chunk_by_overlapping_windows' in content:
        print("⚠️  chunk_by_overlapping_windows method already exists")
        return content
    
    content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    print("✅ Added chunk_by_overlapping_windows method")
    return content

def main():
    """Main migration function"""
    filepath = '/Workspace/Users/jszr.1996@gmail.com/Capstone_project/agent_helper_functions.py'
    
    print("=" * 70)
    print("AGENT_HELPER_FUNCTIONS.PY MIGRATION SCRIPT")
    print("From: Databricks Foundation Model API → To: sentence-transformers")
    print("=" * 70)
    print()
    
    # Step 1: Backup
    print("Step 1: Creating backup...")
    backup_path = backup_file(filepath)
    print()
    
    # Step 2: Read file
    print("Step 2: Reading file...")
    content = read_file(filepath)
    print(f"✅ Read {len(content)} characters")
    print()
    
    # Step 3: Add import
    print("Step 3: Adding sentence-transformers import...")
    content = add_import(content)
    print()
    
    # Step 4: Replace EmbeddingGenerator
    print("Step 4: Replacing EmbeddingGenerator class...")
    content = replace_embedding_generator(content)
    print()
    
    # Step 5: Add new chunking method
    print("Step 5: Adding chunk_by_overlapping_windows method...")
    content = add_overlapping_chunks_method(content)
    print()
    
    # Step 6: Write file
    print("Step 6: Writing updated file...")
    write_file(filepath, content)
    print(f"✅ Updated file written to: {filepath}")
    print()
    
    print("=" * 70)
    print("MIGRATION COMPLETE!")
    print("=" * 70)
    print()
    print("Next steps:")
    print("1. Install sentence-transformers: pip install sentence-transformers")
    print("2. Run database migrations (see DATABASE_MIGRATION_384DIM.md)")
    print("3. Test the updated code:")
    print("   - from agent_helper_functions import EmbeddingGenerator")
    print("   - emb = EmbeddingGenerator()")
    print("   - print(emb.dimension)  # Should be 384")
    print()
    print(f"Backup saved at: {backup_path}")

if __name__ == "__main__":
    main()
