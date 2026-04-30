#!/usr/bin/env python3
"""Migration script to reindex all embeddings with BGE-M3 multi-vector support.

This script:
1. Iterates through all existing chunks in the database
2. Generates new dense embeddings (via native /embedding endpoint + averaging)
3. Generates multi-vector embeddings (ColBERT format: [tokens][1024])
4. Updates the embeddings table with new data
5. Reports progress and statistics

Usage:
    python3 scripts/migrate_to_bge_m3.py
    python3 scripts/migrate_to_bge_m3.py --dry-run  # Preview only
    python3 scripts/migrate_to_bge_m3.py --batch-size 10
"""

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from active_memory_mcp.search.embedder import Embedder
from active_memory_mcp.storage.db import init_db, SessionLocal, Chunk, Embedding
from active_memory_mcp.core.config import config
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def migrate_embeddings(dry_run=False, batch_size=20):
    """Migrate all embeddings to BGE-M3 format with multi-vector support."""
    logger.info("Starting BGE-M3 migration...")
    logger.info(f"Dry run: {dry_run}, Batch size: {batch_size}")

    if not dry_run:
        init_db()

    embedder = Embedder()

    session = SessionLocal() if not dry_run else None
    try:
        if dry_run:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            engine = create_engine("sqlite:///:memory:")
            Session = sessionmaker(bind=engine)
            session = Session()

        total_chunks = session.query(Chunk).count()
        logger.info(f"Found {total_chunks} chunks to process")

        if total_chunks == 0:
            logger.info("No chunks found. Nothing to migrate.")
            return

        processed = 0
        succeeded = 0
        failed = 0

        chunks = session.query(Chunk).all()

        for chunk in chunks:
            processed += 1
            try:
                content = chunk.content
                if not content or not content.strip():
                    logger.warning(f"Chunk {chunk.id}: empty content, skipping")
                    continue

                logger.info(f"Processing chunk {chunk.id} ({processed}/{total_chunks})...")

                if not dry_run:
                    embedding_data = embedder.embed_with_chunking(content)

                    embedding = session.query(Embedding).filter_by(chunk_id=chunk.id).first()
                    if not embedding:
                        embedding = Embedding(chunk_id=chunk.id)
                        session.add(embedding)

                    embedding.embedding = embedding_data.get("dense")
                    embedding.multi_vector = embedding_data.get("multi_vector")
                    embedding.token_count = embedding_data.get("token_count", 0)
                    embedding.model = config.embedding.model
                    embedding.dimensions = config.embedding.dimensions
                    embedding.search_mode = "hybrid"

                    chunk.token_count = embedding_data.get("token_count", 0)

                    succeeded += 1
                    logger.info(f"  ✓ Dense: {len(embedding_data.get('dense', []))} dims, "
                               f"Multi-vector: {embedding_data.get('token_count', 0)} tokens")

                if processed % batch_size == 0:
                    if not dry_run:
                        session.commit()
                        logger.info(f"Committed batch ({processed} chunks processed)")
                    else:
                        logger.info(f"Would commit batch ({processed} chunks)")

            except Exception as e:
                failed += 1
                logger.error(f"Chunk {chunk.id}: error - {e}")
                if not dry_run:
                    session.rollback()

            if dry_run:
                logger.info(f"  Would process chunk {chunk.id}")

        if not dry_run:
            session.commit()
            logger.info("Final commit completed")

        logger.info(f"\nMigration complete!")
        logger.info(f"Processed: {processed}, Succeeded: {succeeded}, Failed: {failed}")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        if session and not dry_run:
            session.rollback()
        raise
    finally:
        if session:
            session.close()


def main():
    parser = argparse.ArgumentParser(description="Migrate embeddings to BGE-M3 multi-vector format")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    parser.add_argument("--batch-size", type=int, default=20, help="Commit batch size (default: 20)")
    args = parser.parse_args()

    logger.info(f"BGE-M3 Migration Script")
    logger.info(f"Model: {config.embedding.model}")
    logger.info(f"Dimensions: {config.embedding.dimensions}")
    logger.info(f"Endpoint: {config.embedding.endpoint_native}")
    logger.info(f"Optimal chunk tokens: {config.embedding.optimal_chunk_tokens}")

    migrate_embeddings(dry_run=args.dry_run, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
