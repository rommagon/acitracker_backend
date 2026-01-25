#!/usr/bin/env python3
"""
Backfill embeddings for publications in the AciTrack database.

This script:
- Iterates over publications missing embeddings
- Generates embeddings via OpenAI API
- Writes embeddings to Postgres
- Is resumable and safe to rerun
- Handles rate limiting with exponential backoff

Usage:
    python scripts/backfill_embeddings.py [OPTIONS]

Options:
    --limit N           Process at most N publications (default: all)
    --since-date DATE   Only process publications from runs after DATE (YYYY-MM-DD)
    --batch-size N      Process N publications per batch (default: 50)
    --dry-run           Show what would be processed without making changes
    --verbose           Enable verbose logging

Environment Variables:
    DATABASE_URL                Postgres connection string (required)
    SPOTITEARLY_LLM_API_KEY     OpenAI API key (required)
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.orm import Session

from db import SessionLocal, TriModelEvent, PublicationEmbedding, init_db, engine
from embeddings import (
    get_openai_client,
    build_embedding_text,
    generate_embeddings_batch,
    chunk_texts_for_batching,
    is_embedding_available,
    EmbeddingError,
    EMBEDDING_MODEL,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_publications_needing_embeddings(
    db: Session,
    limit: Optional[int] = None,
    since_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Get publications that need embeddings generated.

    Returns publications from tri_model_events that don't have embeddings
    in publication_embeddings table.

    Args:
        db: Database session
        limit: Maximum number of publications to return
        since_date: Only include publications from runs after this date

    Returns:
        List of publication dictionaries with required fields
    """
    # Build query to find unique publications without embeddings
    # We get the latest event for each publication_id
    query = """
        WITH latest_events AS (
            SELECT DISTINCT ON (publication_id)
                publication_id,
                title,
                run_id,
                evaluator_rationale,
                final_relevancy_score,
                created_at,
                claude_review_json,
                gemini_review_json,
                gpt_eval_json
            FROM tri_model_events
            WHERE title IS NOT NULL AND title != ''
            ORDER BY publication_id, created_at DESC
        )
        SELECT
            le.publication_id,
            le.title,
            le.run_id,
            le.evaluator_rationale,
            le.final_relevancy_score,
            le.created_at,
            le.claude_review_json,
            le.gemini_review_json,
            le.gpt_eval_json
        FROM latest_events le
        LEFT JOIN publication_embeddings pe ON le.publication_id = pe.publication_id
        WHERE pe.publication_id IS NULL
    """

    params = {}

    if since_date:
        query += " AND le.created_at >= :since_date"
        params["since_date"] = since_date

    query += " ORDER BY le.created_at DESC"

    if limit:
        query += " LIMIT :limit"
        params["limit"] = limit

    result = db.execute(text(query), params)
    rows = result.fetchall()

    publications = []
    for row in rows:
        pub = {
            "publication_id": row[0],
            "title": row[1],
            "run_id": row[2],
            "evaluator_rationale": row[3],
            "final_relevancy_score": row[4],
            "created_at": row[5],
            "claude_review_json": row[6],
            "gemini_review_json": row[7],
            "gpt_eval_json": row[8],
        }

        # Try to extract source and summary from review JSONs
        pub["source"] = None
        pub["final_summary"] = None
        pub["credibility_score"] = None

        for review_json in [pub["claude_review_json"], pub["gemini_review_json"], pub["gpt_eval_json"]]:
            if review_json:
                try:
                    review = json.loads(review_json) if isinstance(review_json, str) else review_json
                    if not pub["source"] and review.get("source"):
                        pub["source"] = review.get("source")
                    if not pub["final_summary"] and review.get("summary"):
                        pub["final_summary"] = review.get("summary")
                    if not pub["credibility_score"] and review.get("credibility_score"):
                        pub["credibility_score"] = review.get("credibility_score")
                except (json.JSONDecodeError, TypeError):
                    pass

        publications.append(pub)

    return publications


def process_batch(
    db: Session,
    publications: List[Dict[str, Any]],
    client,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    Process a batch of publications: generate embeddings and store them.

    Args:
        db: Database session
        publications: List of publication dictionaries
        client: OpenAI client
        dry_run: If True, don't actually store anything

    Returns:
        Tuple of (success_count, error_count)
    """
    success_count = 0
    error_count = 0

    # Build texts for embedding
    items = []
    for pub in publications:
        try:
            text = build_embedding_text(
                title=pub["title"],
                final_summary=pub.get("final_summary"),
                source=pub.get("source"),
                evaluator_rationale=pub.get("evaluator_rationale"),
            )
            items.append((pub["publication_id"], text, pub))
        except ValueError as e:
            logger.warning(f"Skipping {pub['publication_id']}: {e}")
            error_count += 1
            continue

    if not items:
        return success_count, error_count

    if dry_run:
        for pub_id, text, _ in items:
            logger.info(f"[DRY RUN] Would embed {pub_id}: {text[:100]}...")
            success_count += 1
        return success_count, error_count

    # Generate embeddings in batch
    try:
        texts = [item[1] for item in items]
        embeddings = generate_embeddings_batch(texts, client)

        # Store embeddings
        now = datetime.utcnow()
        for i, (pub_id, text, pub) in enumerate(items):
            try:
                embedding = embeddings[i]

                # Create or update PublicationEmbedding
                existing = db.query(PublicationEmbedding).filter(
                    PublicationEmbedding.publication_id == pub_id
                ).first()

                if existing:
                    existing.title = pub["title"]
                    existing.source = pub.get("source")
                    existing.embedded_text = text
                    existing.embedding = embedding
                    existing.embedding_model = EMBEDDING_MODEL
                    existing.embedded_at = now
                    existing.latest_run_id = pub.get("run_id")
                    existing.final_relevancy_score = pub.get("final_relevancy_score")
                    existing.credibility_score = pub.get("credibility_score")
                    existing.final_summary = pub.get("final_summary")
                    existing.updated_at = now
                else:
                    new_embedding = PublicationEmbedding(
                        publication_id=pub_id,
                        title=pub["title"],
                        source=pub.get("source"),
                        embedded_text=text,
                        embedding=embedding,
                        embedding_model=EMBEDDING_MODEL,
                        embedded_at=now,
                        latest_run_id=pub.get("run_id"),
                        final_relevancy_score=pub.get("final_relevancy_score"),
                        credibility_score=pub.get("credibility_score"),
                        final_summary=pub.get("final_summary"),
                    )
                    db.add(new_embedding)

                success_count += 1
                logger.debug(f"Embedded {pub_id}")

            except Exception as e:
                logger.error(f"Error storing embedding for {pub_id}: {e}")
                error_count += 1

        db.commit()

    except EmbeddingError as e:
        logger.error(f"Batch embedding failed: {e}")
        error_count += len(items)

    return success_count, error_count


def main():
    parser = argparse.ArgumentParser(
        description="Backfill embeddings for publications in AciTrack database"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N publications (default: all)",
    )
    parser.add_argument(
        "--since-date",
        type=str,
        default=None,
        help="Only process publications from runs after DATE (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Process N publications per batch (default: 50)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without making changes",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate environment
    if not is_embedding_available():
        logger.error("SPOTITEARLY_LLM_API_KEY not set - cannot generate embeddings")
        sys.exit(1)

    # Initialize database
    logger.info("Initializing database...")
    try:
        init_db()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        sys.exit(1)

    # Get OpenAI client
    client = get_openai_client()
    if not client and not args.dry_run:
        logger.error("Could not create OpenAI client")
        sys.exit(1)

    # Get publications needing embeddings
    logger.info("Finding publications needing embeddings...")
    db = SessionLocal()

    try:
        publications = get_publications_needing_embeddings(
            db,
            limit=args.limit,
            since_date=args.since_date,
        )

        total = len(publications)
        logger.info(f"Found {total} publications needing embeddings")

        if total == 0:
            logger.info("No publications need embeddings - all up to date!")
            return

        if args.dry_run:
            logger.info("[DRY RUN] Would process these publications:")

        # Process in batches
        total_success = 0
        total_errors = 0
        batches = chunk_texts_for_batching(
            [(p["publication_id"], p["title"]) for p in publications],
            max_batch_size=args.batch_size,
        )

        # Map back to full publication data
        pub_by_id = {p["publication_id"]: p for p in publications}
        batch_data = []
        for batch in batches:
            batch_pubs = [pub_by_id[pub_id] for pub_id, _ in batch]
            batch_data.append(batch_pubs)

        for i, batch_pubs in enumerate(batch_data, 1):
            logger.info(f"Processing batch {i}/{len(batch_data)} ({len(batch_pubs)} publications)...")

            success, errors = process_batch(db, batch_pubs, client, dry_run=args.dry_run)
            total_success += success
            total_errors += errors

            logger.info(f"  Batch {i}: {success} success, {errors} errors")

            # Rate limiting pause between batches
            if i < len(batch_data) and not args.dry_run:
                time.sleep(0.5)  # Brief pause between batches

        # Summary
        logger.info("=" * 50)
        logger.info("BACKFILL COMPLETE")
        logger.info(f"  Total processed: {total}")
        logger.info(f"  Successful: {total_success}")
        logger.info(f"  Errors: {total_errors}")

        if args.dry_run:
            logger.info("  (DRY RUN - no changes made)")

    except Exception as e:
        logger.error(f"Backfill failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
