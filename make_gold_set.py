import os
import random
from datetime import datetime
from typing import List, Tuple, Dict

from sqlalchemy import or_
from sqlalchemy.orm import Session

# Import your existing models/session factory
from db import SessionLocal, Publication, CalibrationItem

# Buckets in 0-100 space
BUCKETS = [
    ("80-100", 80, 100),
    ("60-80", 60, 80),
    ("40-60", 40, 60),
    ("20-40", 20, 40),
    ("0-20", 0, 20),
]

GOLD_SET_NAME = os.getenv("GOLD_SET_NAME", "v1")
MODE = os.getenv("GOLD_MODE", "tri-model-daily")
RANDOM_SEED = int(os.getenv("GOLD_RANDOM_SEED", "42"))

DO_INSERT = os.getenv("DO_INSERT", "false").lower() in ("1", "true", "yes")


def normalize_score_to_100(score: float) -> float:
    """
    Your DB has Publication.latest_relevancy_score (Float).
    Some pipelines store it in 0-1; others in 0-100.
    We auto-detect: if it's <= 1.5, treat as 0-1 and scale up.
    """
    if score is None:
        return None
    return score * 100.0 if score <= 1.5 else score


def get_candidates(db: Session) -> List[Dict]:
    """
    Pull candidates from publications with a latest_relevancy_score and decent metadata.
    Exclude anything already in calibration_items (since that would be duplicated/“skipped” anyway).
    """
    existing_pub_ids = {
        row[0]
        for row in db.query(CalibrationItem.publication_id).all()
    }

    pubs = (
        db.query(Publication)
        .filter(Publication.latest_relevancy_score.isnot(None))
        .filter(Publication.title.isnot(None))
        .all()
    )

    out = []
    for p in pubs:
        if p.publication_id in existing_pub_ids:
            continue
        score_100 = normalize_score_to_100(p.latest_relevancy_score)
        if score_100 is None:
            continue
        out.append(
            {
                "publication_id": p.publication_id,
                "title": p.title,
                "source": p.source,
                "published_date": p.published_date,
                "score_100": float(score_100),
                "latest_run_id": p.latest_run_id,
                "url": p.url,
            }
        )
    return out


def pick_two_per_bucket(candidates: List[Dict]) -> List[Dict]:
    """
    Picks 2 items per bucket. If a bucket has <2 items, it relaxes by pulling
    nearest items from the remaining pool (without duplicates).
    """
    random.seed(RANDOM_SEED)

    # We'll avoid duplicates across buckets
    remaining = candidates[:]
    picks: List[Dict] = []

    def in_bucket(c, lo, hi, name):
        s = c["score_100"]
        if name == "80-100":
            return lo <= s <= hi
        return lo <= s < hi

    for bucket_name, lo, hi in BUCKETS:
        bucket = [c for c in remaining if in_bucket(c, lo, hi, bucket_name)]

        if len(bucket) >= 2:
            chosen = random.sample(bucket, 2)
        else:
            # take what we can from the bucket…
            chosen = bucket[:]

            # …then fill with nearest-by-score from what's left
            need = 2 - len(chosen)
            # target = middle of bucket; for 80-100 target near top
            target = 95 if bucket_name == "80-100" else (lo + hi) / 2.0

            # candidates not already chosen
            pool = [c for c in remaining if c not in chosen]
            pool.sort(key=lambda c: abs(c["score_100"] - target))

            chosen += pool[:need]

        # tag + remove from remaining
        for c in chosen:
            c["bucket"] = bucket_name
            picks.append(c)
            if c in remaining:
                remaining.remove(c)

    # Display nicely
    picks.sort(key=lambda x: x["score_100"], reverse=True)
    return picks

def insert_gold_items(db: Session, picks: List[Dict]) -> None:
    """
    Insert into calibration_items with tags.gold=true.
    (This mirrors what your seed endpoint ultimately produces, but does it directly in DB.)
    """
    now = datetime.utcnow()
    for c in picks:
        item = CalibrationItem(
            publication_id=c["publication_id"],
            mode=MODE,
            run_id=c.get("latest_run_id"),
            source=c.get("source"),
            published_date=c.get("published_date"),
            title=c.get("title"),
            abstract=None,  # not available in Publication table as uploaded
            final_relevancy_score=c.get("score_100"),
            final_summary=None,
            tags={
                "gold": True,
                "gold_set": GOLD_SET_NAME,
                "bucket": c["bucket"],
            },
            created_at=now,
            updated_at=now,
        )
        db.add(item)
    db.commit()


def main():
    db = SessionLocal()
    try:
        candidates = get_candidates(db)
        picks = pick_two_per_bucket(candidates)

        print("\n=== GOLD SET PICKS (10 papers) ===\n")
        for i, c in enumerate(picks, 1):
            print(f"{i:02d}. [{c['bucket']}] score={c['score_100']:.1f}  id={c['publication_id']}")
            print(f"    {c['title']}")
            if c.get("source"):
                print(f"    source: {c['source']}")
            if c.get("url"):
                print(f"    url: {c['url']}")
            print()

        if DO_INSERT:
            insert_gold_items(db, picks)
            print(f"Inserted {len(picks)} calibration_items tagged as gold_set='{GOLD_SET_NAME}'.")
        else:
            print("Dry run only (not inserting). Set DO_INSERT=true to insert into calibration_items.")

        # Also print just the IDs for easy seeding via API if you prefer that route
        print("\nPublication IDs (copy/paste):")
        print([c["publication_id"] for c in picks])

    finally:
        db.close()


if __name__ == "__main__":
    main()