#!/usr/bin/env python3
"""
Backfill the canonical publications table from existing data.

This script:
- Populates publications table from publication_embeddings (which has metadata)
- Falls back to tri_model_events for title when not in embeddings
- Uses COALESCE logic to get best available data
- Is resumable and safe to rerun

Usage:
    python scripts/backfill_publications.py [OPTIONS]

Options:
    --limit N           Process at most N publications (default: all)
    --dry-run           Show what would be processed without making changes
    --verbose           Enable verbose logging

Environment Variables:
    DATABASE_URL        Postgres connection string (required)
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from typing import Optional, List, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.orm import Session

from db import SessionLocal, Publication, init_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_publications_to_backfill(
    db: Session,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Get unique publications that need to be in publications table.

    Sources metadata from:
    - publication_embeddings: title, source, published_date (best source)
    - tri_model_events: title (fallback), final_relevancy_score, run_id

    Returns list of publication dicts with all available metadata.
    """
    # Get unique publications with metadata from embeddings and events
    # COALESCE to prefer embeddings data over events data
    query = """
        WITH latest_events AS (
            SELECT DISTINCT ON (publication_id)
                publication_id,
                title,
                run_id,
                final_relevancy_score,
                created_at
            FROM tri_model_events
            WHERE publication_id IS NOT NULL
            ORDER BY publication_id, created_at DESC
        )
        SELECT
            COALESCE(pe.publication_id, le.publication_id) AS publication_id,
            COALESCE(pe.title, le.title) AS title,
            pe.source,
            pe.published_date,
            COALESCE(pe.latest_run_id, le.run_id) AS latest_run_id,
            COALESCE(pe.final_relevancy_score, le.final_relevancy_score) AS latest_relevancy_score,
            pe.credibility_score AS latest_credibility_score
        FROM latest_events le
        FULL OUTER JOIN publication_embeddings pe ON pe.publication_id = le.publication_id
        LEFT JOIN publications p ON p.publication_id = COALESCE(pe.publication_id, le.publication_id)
        WHERE p.publication_id IS NULL
          AND COALESCE(pe.title, le.title) IS NOT NULL
          AND COALESCE(pe.title, le.title) != ''
        ORDER BY le.created_at DESC NULLS LAST
    """

    params = {}
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
            "source": row[2],
            "published_date": row[3],
            "latest_run_id": row[4],
            "latest_relevancy_score": row[5],
            "latest_credibility_score": row[6],
            "url": None,  # No canonical URL source available
        }
        publications.append(pub)

    return publications


def backfill_publications(
    db: Session,
    publications: List[Dict[str, Any]],
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    Insert publications into the canonical publications table.

    Returns (success_count, error_count).
    """
    success_count = 0
    error_count = 0
    now = datetime.utcnow()

    for pub in publications:
        try:
            if dry_run:
                logger.info(
                    f"[DRY RUN] Would insert: {pub['publication_id']} - "
                    f"source={pub['source']}, date={pub['published_date']}"
                )
                success_count += 1
                continue

            # Check if already exists (shouldn't due to query, but safe)
            existing = db.query(Publication).filter(
                Publication.publication_id == pub["publication_id"]
            ).first()

            if existing:
                # Update existing
                if pub["title"]:
                    existing.title = pub["title"]
                if pub["source"]:
                    existing.source = pub["source"]
                if pub["published_date"]:
                    existing.published_date = pub["published_date"]
                if pub["url"]:
                    existing.url = pub["url"]
                if pub["latest_run_id"]:
                    existing.latest_run_id = pub["latest_run_id"]
                if pub["latest_relevancy_score"] is not None:
                    existing.latest_relevancy_score = pub["latest_relevancy_score"]
                if pub["latest_credibility_score"] is not None:
                    existing.latest_credibility_score = pub["latest_credibility_score"]
                existing.updated_at = now
            else:
                # Insert new
                new_pub = Publication(
                    publication_id=pub["publication_id"],
                    title=pub["title"],
                    source=pub["source"],
                    published_date=pub["published_date"],
                    url=pub["url"],
                    latest_run_id=pub["latest_run_id"],
                    latest_relevancy_score=pub["latest_relevancy_score"],
                    latest_credibility_score=pub["latest_credibility_score"],
                )
                db.add(new_pub)

            success_count += 1

        except Exception as e:
            logger.error(f"Error inserting {pub['publication_id']}: {e}")
            error_count += 1

    if not dry_run:
        db.commit()

    return success_count, error_count


def report_coverage(db: Session):
    """Report metadata coverage in the publications table."""
    query = """
        SELECT
            COUNT(*) AS total,
            COUNT(source) AS with_source,
            COUNT(published_date) AS with_date,
            COUNT(url) AS with_url,
            COUNT(latest_run_id) AS with_run_id
        FROM publications
    """
    result = db.execute(text(query))
    row = result.fetchone()

    total = row[0] or 0
    with_source = row[1] or 0
    with_date = row[2] or 0
    with_url = row[3] or 0
    with_run_id = row[4] or 0

    logger.info("=" * 50)
    logger.info("PUBLICATIONS TABLE COVERAGE")
    logger.info(f"  Total publications: {total}")
    if total > 0:
        logger.info(f"  With source: {with_source} ({100*with_source/total:.1f}%)")
        logger.info(f"  With date: {with_date} ({100*with_date/total:.1f}%)")
        logger.info(f"  With URL: {with_url} ({100*with_url/total:.1f}%)")
        logger.info(f"  With run_id: {with_run_id} ({100*with_run_id/total:.1f}%)")
    else:
        logger.info("  (no publications)")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill canonical publications table from existing data"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N publications (default: all)",
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

    # Initialize database
    logger.info("Initializing database...")
    try:
        init_db()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        sys.exit(1)

    # Get publications to backfill
    logger.info("Finding publications to backfill...")
    db = SessionLocal()

    try:
        publications = get_publications_to_backfill(db, limit=args.limit)
        total = len(publications)
        logger.info(f"Found {total} publications to backfill")

        if total == 0:
            logger.info("No publications need backfilling - all up to date!")
            report_coverage(db)
            return

        # Preview metadata availability in this batch
        with_source = sum(1 for p in publications if p["source"])
        with_date = sum(1 for p in publications if p["published_date"])
        with_url = sum(1 for p in publications if p["url"])
        logger.info(f"In this batch:")
        logger.info(f"  With source: {with_source}/{total} ({100*with_source/total:.1f}%)")
        logger.info(f"  With date: {with_date}/{total} ({100*with_date/total:.1f}%)")
        logger.info(f"  With URL: {with_url}/{total} ({100*with_url/total:.1f}%)")

        if args.dry_run:
            logger.info("[DRY RUN] Would process these publications:")

        # Backfill
        success, errors = backfill_publications(db, publications, dry_run=args.dry_run)

        # Summary
        logger.info("=" * 50)
        logger.info("BACKFILL COMPLETE")
        logger.info(f"  Total processed: {total}")
        logger.info(f"  Successful: {success}")
        logger.info(f"  Errors: {errors}")

        if args.dry_run:
            logger.info("  (DRY RUN - no changes made)")
        else:
            # Report final coverage
            report_coverage(db)

    except Exception as e:
        logger.error(f"Backfill failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
