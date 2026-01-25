#!/usr/bin/env python3
"""
Backfill the canonical publications table from tri_model_events.

This script:
- Extracts unique publications from tri_model_events
- Populates the publications table with metadata (title, source, published_date)
- Extracts metadata from review JSONs when available
- Updates latest_run_id and scores

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
import json
import logging
import os
import sys
from datetime import datetime
from typing import Optional, List, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.orm import Session

from db import SessionLocal, Publication, TriModelEvent, init_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def extract_metadata_from_review_json(review_json: Optional[str]) -> Dict[str, Any]:
    """
    Extract source, published_date, url, and credibility_score from a review JSON.

    Returns dict with extracted values (may contain None values).
    """
    result = {
        "source": None,
        "published_date": None,
        "url": None,
        "credibility_score": None,
    }

    if not review_json:
        return result

    try:
        review = json.loads(review_json) if isinstance(review_json, str) else review_json

        if review.get("source"):
            result["source"] = str(review["source"])[:255]  # Limit length

        if review.get("published_date"):
            pub_date_str = review["published_date"]
            if isinstance(pub_date_str, str):
                try:
                    result["published_date"] = datetime.fromisoformat(
                        pub_date_str.replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

        if review.get("url"):
            result["url"] = str(review["url"])

        if review.get("credibility_score") is not None:
            try:
                result["credibility_score"] = float(review["credibility_score"])
            except (ValueError, TypeError):
                pass

    except (json.JSONDecodeError, TypeError, AttributeError):
        pass

    return result


def get_publications_to_backfill(
    db: Session,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Get unique publications from tri_model_events that need to be in publications table.

    Returns list of publication dicts with all available metadata.
    """
    # Get the latest event for each publication_id with all metadata
    query = """
        WITH latest_events AS (
            SELECT DISTINCT ON (publication_id)
                publication_id,
                title,
                run_id,
                final_relevancy_score,
                claude_review_json,
                gemini_review_json,
                gpt_eval_json,
                created_at
            FROM tri_model_events
            WHERE publication_id IS NOT NULL AND title IS NOT NULL AND title != ''
            ORDER BY publication_id, created_at DESC
        )
        SELECT
            le.publication_id,
            le.title,
            le.run_id,
            le.final_relevancy_score,
            le.claude_review_json,
            le.gemini_review_json,
            le.gpt_eval_json
        FROM latest_events le
        LEFT JOIN publications p ON p.publication_id = le.publication_id
        WHERE p.publication_id IS NULL
        ORDER BY le.created_at DESC
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
            "run_id": row[2],
            "final_relevancy_score": row[3],
            "claude_review_json": row[4],
            "gemini_review_json": row[5],
            "gpt_eval_json": row[6],
            "source": None,
            "published_date": None,
            "url": None,
            "credibility_score": None,
        }

        # Extract metadata from review JSONs (try each until we get values)
        for review_json in [pub["claude_review_json"], pub["gemini_review_json"], pub["gpt_eval_json"]]:
            metadata = extract_metadata_from_review_json(review_json)

            if not pub["source"] and metadata["source"]:
                pub["source"] = metadata["source"]
            if not pub["published_date"] and metadata["published_date"]:
                pub["published_date"] = metadata["published_date"]
            if not pub["url"] and metadata["url"]:
                pub["url"] = metadata["url"]
            if pub["credibility_score"] is None and metadata["credibility_score"] is not None:
                pub["credibility_score"] = metadata["credibility_score"]

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
                existing.title = pub["title"]
                if pub["source"]:
                    existing.source = pub["source"]
                if pub["published_date"]:
                    existing.published_date = pub["published_date"]
                if pub["url"]:
                    existing.url = pub["url"]
                existing.latest_run_id = pub["run_id"]
                existing.latest_relevancy_score = pub["final_relevancy_score"]
                if pub["credibility_score"] is not None:
                    existing.latest_credibility_score = pub["credibility_score"]
                existing.updated_at = now
            else:
                # Insert new
                new_pub = Publication(
                    publication_id=pub["publication_id"],
                    title=pub["title"],
                    source=pub["source"],
                    published_date=pub["published_date"],
                    url=pub["url"],
                    latest_run_id=pub["run_id"],
                    latest_relevancy_score=pub["final_relevancy_score"],
                    latest_credibility_score=pub["credibility_score"],
                )
                db.add(new_pub)

            success_count += 1

        except Exception as e:
            logger.error(f"Error inserting {pub['publication_id']}: {e}")
            error_count += 1

    if not dry_run:
        db.commit()

    return success_count, error_count


def main():
    parser = argparse.ArgumentParser(
        description="Backfill canonical publications table from tri_model_events"
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
            return

        # Preview some metadata
        with_source = sum(1 for p in publications if p["source"])
        with_date = sum(1 for p in publications if p["published_date"])
        with_url = sum(1 for p in publications if p["url"])
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

    except Exception as e:
        logger.error(f"Backfill failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
