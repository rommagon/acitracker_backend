"""
Calibration Tool API endpoints for human relevancy labeling.
Allows employees to rate publications and calibrate tri-model scoring.
"""

import os
import json
import logging
import random
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends, Query, Security
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import func, and_, or_, desc
from sqlalchemy.orm import Session

from db import (
    get_db,
    CalibrationItem,
    HumanEvaluation,
    TriModelEvent,
    MustRead,
    Publication,
    PublicationEmbedding,
    Run,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calibration", tags=["calibration"])

# API Key configuration (same as main.py)
ACITRACK_API_KEY = os.getenv("ACITRACK_API_KEY")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: Optional[str] = Security(api_key_header)):
    """Verify API key for protected endpoints."""
    if not ACITRACK_API_KEY:
        return None
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    if api_key != ACITRACK_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return api_key


# ----- Pydantic Models -----

class SeedItemsRequest(BaseModel):
    publication_ids: List[str] = Field(..., min_length=1)
    run_id: Optional[str] = None
    mode: Optional[str] = "tri-model-daily"
    tags: Optional[Dict[str, Any]] = None


class SeedMustReadsRequest(BaseModel):
    run_id: Optional[str] = None
    mode: Optional[str] = "tri-model-daily"
    tags: Optional[Dict[str, Any]] = None


class SubmitEvaluationRequest(BaseModel):
    calibration_item_id: str
    evaluator: str = Field(..., min_length=1)
    human_score: int = Field(..., ge=0, le=100)
    reasoning: str = Field(..., min_length=1)
    confidence: Optional[str] = Field(None, pattern="^(low|medium|high)$")


class CalibrationItemResponse(BaseModel):
    calibration_item_id: str
    publication_id: str
    title: Optional[str]
    source: Optional[str]
    published_date: Optional[str]
    final_relevancy_score: Optional[float]
    final_summary: Optional[str]
    run_id: Optional[str]
    mode: Optional[str]
    tags: Optional[Dict[str, Any]]


# ----- Helper Functions -----

def fetch_publication_details(
    db: Session,
    publication_id: str,
    run_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Fetch publication details from existing tables.
    Tries tri_model_events first, then publications table.
    """
    details = {
        "title": None,
        "source": None,
        "published_date": None,
        "final_relevancy_score": None,
        "final_summary": None,
        "run_id": None,
        "mode": None,
    }

    # Try tri_model_events first
    query = db.query(TriModelEvent).filter(
        TriModelEvent.publication_id == publication_id
    )
    if run_id:
        query = query.filter(TriModelEvent.run_id == run_id)

    event = query.order_by(desc(TriModelEvent.created_at)).first()

    if event:
        details["title"] = event.title
        details["final_relevancy_score"] = event.final_relevancy_score
        details["run_id"] = event.run_id
        details["mode"] = event.mode

    # Try publications table for source/date
    pub = db.query(Publication).filter(
        Publication.publication_id == publication_id
    ).first()

    if pub:
        if not details["title"]:
            details["title"] = pub.title
        details["source"] = pub.source
        details["published_date"] = pub.published_date
        if not details["final_relevancy_score"]:
            details["final_relevancy_score"] = pub.latest_relevancy_score
        if not details["run_id"]:
            details["run_id"] = pub.latest_run_id

    # Try publication_embeddings for summary
    emb = db.query(PublicationEmbedding).filter(
        PublicationEmbedding.publication_id == publication_id
    ).first()

    if emb:
        details["final_summary"] = emb.final_summary
        if not details["source"]:
            details["source"] = emb.source
        if not details["published_date"]:
            details["published_date"] = emb.published_date

    return details


def get_score_bucket(score: Optional[float]) -> int:
    """Get bucket index (0-4) for a relevancy score."""
    if score is None:
        return 2  # Middle bucket for unknown
    if score < 20:
        return 0
    elif score < 40:
        return 1
    elif score < 60:
        return 2
    elif score < 80:
        return 3
    else:
        return 4


# ----- API Endpoints -----

@router.post("/items/seed")
async def seed_calibration_items(
    request: SeedItemsRequest,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    Seed calibration items from a list of publication IDs.
    Upserts into calibration_items, fetching details from existing storage.
    """
    seeded = 0
    skipped_existing = 0

    for pub_id in request.publication_ids:
        # Check if already exists
        existing = db.query(CalibrationItem).filter(
            CalibrationItem.publication_id == pub_id
        ).first()

        if existing:
            # Update tags if provided
            if request.tags:
                existing_tags = existing.tags or {}
                existing_tags.update(request.tags)
                existing.tags = existing_tags
                existing.updated_at = datetime.utcnow()
            skipped_existing += 1
            continue

        # Fetch publication details
        details = fetch_publication_details(db, pub_id, request.run_id)

        # Create new calibration item
        item = CalibrationItem(
            publication_id=pub_id,
            mode=request.mode or details.get("mode"),
            run_id=request.run_id or details.get("run_id"),
            source=details.get("source"),
            published_date=details.get("published_date"),
            title=details.get("title"),
            final_relevancy_score=details.get("final_relevancy_score"),
            final_summary=details.get("final_summary"),
            tags=request.tags,
        )
        db.add(item)
        seeded += 1

    db.commit()

    return {
        "seeded": seeded,
        "skipped_existing": skipped_existing
    }


@router.post("/items/seed-mustreads")
async def seed_mustreads(
    request: SeedMustReadsRequest,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    Seed calibration items from the latest run's must-reads.
    """
    # Get the run_id
    run_id = request.run_id
    if not run_id:
        # Get latest run for mode
        run = db.query(Run).filter(
            Run.mode == request.mode
        ).order_by(desc(Run.started_at)).first()

        if not run:
            raise HTTPException(
                status_code=404,
                detail=f"No runs found for mode={request.mode}"
            )
        run_id = run.run_id

    # Get must-reads for this run
    must_read = db.query(MustRead).filter(MustRead.run_id == run_id).first()

    if not must_read:
        raise HTTPException(
            status_code=404,
            detail=f"No must-reads found for run_id={run_id}"
        )

    # Parse must-reads JSON
    try:
        must_reads_data = json.loads(must_read.must_reads_json)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=500,
            detail="Invalid must_reads JSON"
        )

    # Extract publication IDs
    publication_ids = []
    for item in must_reads_data:
        if isinstance(item, dict) and "publication_id" in item:
            publication_ids.append(item["publication_id"])
        elif isinstance(item, str):
            publication_ids.append(item)

    if not publication_ids:
        return {"seeded": 0, "skipped_existing": 0, "message": "No publication IDs found in must-reads"}

    # Build tags
    tags = request.tags or {}
    tags["source"] = "mustreads"
    tags["run_id"] = run_id

    # Use the seed endpoint logic
    seed_request = SeedItemsRequest(
        publication_ids=publication_ids,
        run_id=run_id,
        mode=request.mode,
        tags=tags
    )

    return await seed_calibration_items(seed_request, db, api_key)


@router.get("/next")
async def get_next_item(
    evaluator: str = Query(..., min_length=1),
    strategy: str = Query(default="balanced", pattern="^(balanced|gold_first|random)$"),
    include_gold: bool = Query(default=True),
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
) -> Optional[CalibrationItemResponse]:
    """
    Get the next calibration item for an evaluator to rate.
    Returns None if all items have been rated.
    """
    # Get IDs already rated by this evaluator
    rated_ids = db.query(HumanEvaluation.calibration_item_id).filter(
        HumanEvaluation.evaluator == evaluator
    ).subquery()

    # Base query for unrated items
    base_query = db.query(CalibrationItem).filter(
        ~CalibrationItem.id.in_(rated_ids)
    )

    item = None

    if strategy == "gold_first":
        # Try gold items first
        item = base_query.filter(
            CalibrationItem.tags.op("->")("gold").astext == "true"
        ).order_by(func.random()).first()

        if not item:
            # Fall back to any unrated item
            item = base_query.order_by(func.random()).first()

    elif strategy == "balanced":
        # Stratify by score buckets
        # Count items per bucket that evaluator hasn't rated
        buckets = [
            (0, 20),
            (20, 40),
            (40, 60),
            (60, 80),
            (80, 101),
        ]

        # Try to get one from each bucket in round-robin fashion
        # Find the bucket with the most unrated items
        for low, high in buckets:
            bucket_query = base_query.filter(
                and_(
                    CalibrationItem.final_relevancy_score >= low,
                    CalibrationItem.final_relevancy_score < high
                )
            )
            candidate = bucket_query.order_by(func.random()).first()
            if candidate:
                item = candidate
                break

        # Also check for items with no score
        if not item:
            item = base_query.filter(
                CalibrationItem.final_relevancy_score.is_(None)
            ).order_by(func.random()).first()

        # Fall back to any unrated item
        if not item:
            item = base_query.order_by(func.random()).first()

    else:  # random
        item = base_query.order_by(func.random()).first()

    if not item:
        return None

    return CalibrationItemResponse(
        calibration_item_id=str(item.id),
        publication_id=item.publication_id,
        title=item.title,
        source=item.source,
        published_date=item.published_date.isoformat() if item.published_date else None,
        final_relevancy_score=item.final_relevancy_score,
        final_summary=item.final_summary,
        run_id=item.run_id,
        mode=item.mode,
        tags=item.tags,
    )


@router.post("/submit")
async def submit_evaluation(
    request: SubmitEvaluationRequest,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    Submit a human evaluation for a calibration item.
    """
    # Validate calibration_item_id
    try:
        item_uuid = UUID(request.calibration_item_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid calibration_item_id format")

    # Check item exists
    item = db.query(CalibrationItem).filter(
        CalibrationItem.id == item_uuid
    ).first()

    if not item:
        raise HTTPException(
            status_code=404,
            detail=f"Calibration item not found: {request.calibration_item_id}"
        )

    # Check for existing evaluation
    existing = db.query(HumanEvaluation).filter(
        and_(
            HumanEvaluation.calibration_item_id == item_uuid,
            HumanEvaluation.evaluator == request.evaluator
        )
    ).first()

    if existing:
        raise HTTPException(
            status_code=409,
            detail="You have already rated this item"
        )

    # Create evaluation
    evaluation = HumanEvaluation(
        calibration_item_id=item_uuid,
        evaluator=request.evaluator,
        human_score=request.human_score,
        reasoning=request.reasoning,
        confidence=request.confidence,
    )
    db.add(evaluation)
    db.commit()

    return {
        "status": "ok",
        "llm_score": item.final_relevancy_score
    }


@router.get("/stats")
async def get_stats(
    evaluator: Optional[str] = None,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    Get calibration statistics.
    """
    total_items = db.query(func.count(CalibrationItem.id)).scalar()

    # Count gold items
    gold_count = db.query(func.count(CalibrationItem.id)).filter(
        CalibrationItem.tags.op("->")("gold").astext == "true"
    ).scalar()

    if evaluator:
        # Stats for specific evaluator
        total_rated = db.query(func.count(HumanEvaluation.id)).filter(
            HumanEvaluation.evaluator == evaluator
        ).scalar()

        avg_score = db.query(func.avg(HumanEvaluation.human_score)).filter(
            HumanEvaluation.evaluator == evaluator
        ).scalar()

        gold_rated = db.query(func.count(HumanEvaluation.id)).join(
            CalibrationItem
        ).filter(
            and_(
                HumanEvaluation.evaluator == evaluator,
                CalibrationItem.tags.op("->")("gold").astext == "true"
            )
        ).scalar()
    else:
        # Overall stats
        total_rated = db.query(func.count(HumanEvaluation.id)).scalar()
        avg_score = db.query(func.avg(HumanEvaluation.human_score)).scalar()
        gold_rated = db.query(func.count(HumanEvaluation.id)).join(
            CalibrationItem
        ).filter(
            CalibrationItem.tags.op("->")("gold").astext == "true"
        ).scalar()

    # Distribution buckets
    distribution = {
        "0-20": 0,
        "20-40": 0,
        "40-60": 0,
        "60-80": 0,
        "80-100": 0,
    }

    bucket_query = db.query(HumanEvaluation.human_score)
    if evaluator:
        bucket_query = bucket_query.filter(HumanEvaluation.evaluator == evaluator)

    scores = [row[0] for row in bucket_query.all()]
    for score in scores:
        if score < 20:
            distribution["0-20"] += 1
        elif score < 40:
            distribution["20-40"] += 1
        elif score < 60:
            distribution["40-60"] += 1
        elif score < 80:
            distribution["60-80"] += 1
        else:
            distribution["80-100"] += 1

    return {
        "total_items": total_items,
        "total_rated": total_rated,
        "remaining": total_items - (total_rated if evaluator else db.query(
            func.count(func.distinct(HumanEvaluation.calibration_item_id))
        ).scalar()),
        "avg_score": round(avg_score, 2) if avg_score else None,
        "distribution": distribution,
        "gold_total": gold_count,
        "gold_rated": gold_rated,
        "evaluator": evaluator,
    }


@router.get("/export")
async def export_data(
    format: str = Query(default="csv", pattern="^(csv|jsonl)$"),
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    Export calibration data as CSV or JSON lines.
    """
    # Query joined data
    results = db.query(
        CalibrationItem.publication_id,
        CalibrationItem.title,
        CalibrationItem.source,
        CalibrationItem.final_relevancy_score,
        CalibrationItem.tags,
        HumanEvaluation.evaluator,
        HumanEvaluation.human_score,
        HumanEvaluation.reasoning,
        HumanEvaluation.confidence,
        HumanEvaluation.created_at,
    ).join(
        HumanEvaluation,
        CalibrationItem.id == HumanEvaluation.calibration_item_id
    ).order_by(
        CalibrationItem.publication_id,
        HumanEvaluation.created_at
    ).all()

    if format == "csv":
        import io
        output = io.StringIO()

        # Header
        output.write("publication_id,title,source,final_relevancy_score,human_score,reasoning,evaluator,confidence,created_at,tags\n")

        for row in results:
            # Escape CSV fields
            title = (row[1] or "").replace('"', '""')
            source = (row[2] or "").replace('"', '""')
            reasoning = (row[7] or "").replace('"', '""')
            tags = json.dumps(row[4]) if row[4] else ""

            output.write(f'"{row[0]}","{title}","{source}",{row[3] or ""},')
            output.write(f'{row[6]},"{reasoning}","{row[5]}","{row[8] or ""}",')
            output.write(f'"{row[9].isoformat() if row[9] else ""}","{tags}"\n')

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=calibration_export.csv"}
        )

    else:  # jsonl
        import io
        output = io.StringIO()

        for row in results:
            record = {
                "publication_id": row[0],
                "title": row[1],
                "source": row[2],
                "final_relevancy_score": row[3],
                "human_score": row[6],
                "reasoning": row[7],
                "evaluator": row[5],
                "confidence": row[8],
                "created_at": row[9].isoformat() if row[9] else None,
                "tags": row[4],
            }
            output.write(json.dumps(record) + "\n")

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=calibration_export.jsonl"}
        )


@router.get("/items")
async def list_items(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    gold_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    List calibration items with optional filters.
    """
    query = db.query(CalibrationItem)

    if gold_only:
        query = query.filter(
            CalibrationItem.tags.op("->")("gold").astext == "true"
        )

    total = query.count()
    items = query.order_by(desc(CalibrationItem.created_at)).offset(offset).limit(limit).all()

    return {
        "total": total,
        "items": [
            {
                "id": str(item.id),
                "publication_id": item.publication_id,
                "title": item.title,
                "source": item.source,
                "final_relevancy_score": item.final_relevancy_score,
                "tags": item.tags,
                "created_at": item.created_at.isoformat(),
            }
            for item in items
        ]
    }


# ----- HTML UI -----

CALIBRATION_UI_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Science Agent - Calibration Tool</title>
    <style>
        :root {
            --primary: #0066cc;
            --primary-dark: #004d99;
            --primary-light: #e6f0ff;
            --accent: #00a86b;
            --accent-light: #e6fff5;
            --warning: #f59e0b;
            --warning-light: #fef3c7;
            --error: #dc2626;
            --error-light: #fef2f2;
            --success: #22c55e;
            --success-light: #f0fdf4;
            --gray-50: #f9fafb;
            --gray-100: #f3f4f6;
            --gray-200: #e5e7eb;
            --gray-500: #6b7280;
            --gray-700: #374151;
            --gray-900: #111827;
            --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
            --shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06);
            --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.1), 0 4px 6px -2px rgba(0,0,0,0.05);
            --radius: 12px;
            --radius-sm: 8px;
        }
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 24px 16px;
            background: linear-gradient(135deg, var(--gray-50) 0%, var(--gray-100) 100%);
            color: var(--gray-900);
            min-height: 100vh;
            line-height: 1.6;
        }
        .header {
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 24px;
            padding: 20px 24px;
            background: white;
            border-radius: var(--radius);
            box-shadow: var(--shadow);
        }
        .logo {
            width: 56px;
            height: 56px;
            border-radius: var(--radius-sm);
            object-fit: contain;
        }
        .header-text h1 {
            font-size: 24px;
            font-weight: 700;
            color: var(--primary);
            margin-bottom: 4px;
        }
        .header-text .subtitle {
            font-size: 14px;
            color: var(--gray-500);
        }
        .card {
            background: white;
            border-radius: var(--radius);
            padding: 28px;
            margin-bottom: 20px;
            box-shadow: var(--shadow);
            border: 1px solid var(--gray-200);
        }
        .card h2 {
            font-size: 20px;
            font-weight: 600;
            color: var(--gray-900);
            margin-bottom: 16px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        .form-group label {
            display: block;
            font-size: 14px;
            font-weight: 500;
            color: var(--gray-700);
            margin-bottom: 8px;
        }
        input[type="text"],
        input[type="password"] {
            width: 100%;
            padding: 12px 16px;
            border: 2px solid var(--gray-200);
            border-radius: var(--radius-sm);
            font-size: 16px;
            transition: border-color 0.2s, box-shadow 0.2s;
        }
        input[type="text"]:focus,
        input[type="password"]:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px var(--primary-light);
        }
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            background: var(--primary);
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: var(--radius-sm);
            font-size: 16px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn:hover {
            background: var(--primary-dark);
            transform: translateY(-1px);
            box-shadow: var(--shadow);
        }
        .btn:active {
            transform: translateY(0);
        }
        .btn:disabled {
            background: var(--gray-200);
            color: var(--gray-500);
            cursor: not-allowed;
            transform: none;
        }
        .btn-secondary {
            background: var(--gray-100);
            color: var(--gray-700);
        }
        .btn-secondary:hover {
            background: var(--gray-200);
        }
        .paper-card {
            border-left: 4px solid var(--primary);
        }
        .paper-title {
            font-size: 20px;
            font-weight: 600;
            color: var(--gray-900);
            margin-bottom: 12px;
            line-height: 1.4;
        }
        .paper-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            color: var(--gray-500);
            font-size: 14px;
            margin-bottom: 20px;
        }
        .paper-meta span {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .divider {
            border: none;
            border-top: 1px solid var(--gray-200);
            margin: 24px 0;
        }
        .summary-section h3 {
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--gray-500);
            margin-bottom: 12px;
        }
        .summary-text {
            font-size: 15px;
            line-height: 1.7;
            color: var(--gray-700);
            background: var(--gray-50);
            padding: 16px;
            border-radius: var(--radius-sm);
        }
        .slider-container {
            display: flex;
            align-items: center;
            gap: 20px;
        }
        .slider-container input[type="range"] {
            flex: 1;
            height: 8px;
            -webkit-appearance: none;
            appearance: none;
            background: linear-gradient(to right, var(--error) 0%, var(--warning) 50%, var(--success) 100%);
            border-radius: 4px;
            cursor: pointer;
        }
        .slider-container input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 28px;
            height: 28px;
            background: white;
            border: 3px solid var(--primary);
            border-radius: 50%;
            cursor: pointer;
            box-shadow: var(--shadow);
            transition: transform 0.2s;
        }
        .slider-container input[type="range"]::-webkit-slider-thumb:hover {
            transform: scale(1.1);
        }
        .slider-container input[type="range"]::-moz-range-thumb {
            width: 28px;
            height: 28px;
            background: white;
            border: 3px solid var(--primary);
            border-radius: 50%;
            cursor: pointer;
            box-shadow: var(--shadow);
        }
        .score-display {
            font-size: 28px;
            font-weight: 700;
            color: var(--primary);
            min-width: 60px;
            text-align: center;
            background: var(--primary-light);
            padding: 8px 16px;
            border-radius: var(--radius-sm);
        }
        textarea {
            width: 100%;
            padding: 14px 16px;
            border: 2px solid var(--gray-200);
            border-radius: var(--radius-sm);
            font-size: 15px;
            font-family: inherit;
            resize: vertical;
            min-height: 100px;
            line-height: 1.6;
            transition: border-color 0.2s, box-shadow 0.2s;
        }
        textarea:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px var(--primary-light);
        }
        select {
            width: 100%;
            padding: 12px 16px;
            border: 2px solid var(--gray-200);
            border-radius: var(--radius-sm);
            font-size: 16px;
            background: white;
            cursor: pointer;
            transition: border-color 0.2s;
        }
        select:focus {
            outline: none;
            border-color: var(--primary);
        }
        .result-card {
            background: var(--success-light);
            border: 2px solid var(--success);
            text-align: center;
        }
        .result-card.mismatch {
            background: var(--warning-light);
            border-color: var(--warning);
        }
        .result-card h2 {
            color: var(--success);
        }
        .result-card.mismatch h2 {
            color: var(--warning);
        }
        .score-comparison {
            display: flex;
            justify-content: center;
            gap: 48px;
            margin: 28px 0;
        }
        .score-box {
            text-align: center;
        }
        .score-box .label {
            font-size: 13px;
            font-weight: 500;
            color: var(--gray-500);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }
        .score-box .value {
            font-size: 40px;
            font-weight: 700;
        }
        .score-box.human .value {
            color: var(--primary);
        }
        .score-box.llm .value {
            color: var(--accent);
        }
        .hidden {
            display: none !important;
        }
        .loading {
            text-align: center;
            padding: 48px 24px;
            color: var(--gray-500);
        }
        .loading::before {
            content: "";
            display: block;
            width: 40px;
            height: 40px;
            margin: 0 auto 16px;
            border: 3px solid var(--gray-200);
            border-top-color: var(--primary);
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        .done-card {
            text-align: center;
            padding: 48px 24px;
        }
        .done-card h2 {
            color: var(--success);
            font-size: 28px;
            margin-bottom: 12px;
        }
        .done-card p {
            color: var(--gray-500);
            margin-bottom: 24px;
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 16px;
            margin: 24px 0;
        }
        .stat-box {
            text-align: center;
            padding: 20px 16px;
            background: var(--gray-50);
            border-radius: var(--radius-sm);
            border: 1px solid var(--gray-200);
        }
        .stat-box .value {
            font-size: 28px;
            font-weight: 700;
            color: var(--primary);
        }
        .stat-box .label {
            font-size: 11px;
            font-weight: 600;
            color: var(--gray-500);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 4px;
        }
        .gold-badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            background: var(--warning-light);
            color: #92400e;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
        }
        .error {
            color: var(--error);
            padding: 14px 16px;
            background: var(--error-light);
            border-radius: var(--radius-sm);
            margin-bottom: 16px;
            font-size: 14px;
            border: 1px solid var(--error);
        }
        .api-key-section {
            background: var(--gray-50);
            padding: 16px;
            border-radius: var(--radius-sm);
            margin-bottom: 20px;
            border: 1px solid var(--gray-200);
        }
        .api-key-section label {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
            color: var(--gray-500);
            margin-bottom: 8px;
        }
        .api-key-section input {
            font-size: 14px;
            padding: 10px 14px;
        }
        .api-key-status {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            margin-top: 8px;
        }
        .api-key-status.valid {
            color: var(--success);
        }
        .api-key-status.invalid {
            color: var(--error);
        }
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: currentColor;
        }
        @media (max-width: 600px) {
            body {
                padding: 16px 12px;
            }
            .header {
                flex-direction: column;
                text-align: center;
            }
            .score-comparison {
                gap: 24px;
            }
            .stats {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="header">
        <img src="/static/logo.png" alt="Science Agent Logo" class="logo" onerror="this.style.display='none'">
        <div class="header-text">
            <h1>Science Agent Calibration</h1>
            <p class="subtitle">Help calibrate AI relevancy scoring by rating publications</p>
        </div>
    </div>

    <!-- Setup Section -->
    <div id="setup-section" class="card">
        <h2>Welcome!</h2>
        <div class="api-key-section">
            <label>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                    <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                </svg>
                API Key (required)
            </label>
            <input type="password" id="api-key-input" placeholder="Enter your API key">
            <div id="api-key-status" class="api-key-status hidden">
                <span class="status-dot"></span>
                <span class="status-text"></span>
            </div>
        </div>
        <div class="form-group">
            <label>Your Name or Email</label>
            <input type="text" id="evaluator-input" placeholder="e.g., john@example.com">
        </div>
        <button class="btn" onclick="startCalibration()">Start Rating</button>
    </div>

    <!-- Rating Section -->
    <div id="rating-section" class="card paper-card hidden">
        <div id="loading" class="loading">Loading next paper...</div>
        <div id="paper-content" class="hidden">
            <div class="paper-title" id="paper-title"></div>
            <div class="paper-meta">
                <span id="paper-source"></span>
                <span id="paper-date"></span>
                <span id="gold-badge" class="gold-badge hidden">⭐ Gold Standard</span>
            </div>
            <hr class="divider">
            <div class="summary-section">
                <h3>AI Summary</h3>
                <p class="summary-text" id="paper-summary"></p>
            </div>
            <hr class="divider">
            <div class="form-group">
                <label>Relevancy Score (0-100)</label>
                <div class="slider-container">
                    <input type="range" id="score-slider" min="0" max="100" value="50" oninput="updateScoreDisplay()">
                    <div class="score-display" id="score-display">50</div>
                </div>
            </div>
            <div class="form-group">
                <label>Reasoning (1-3 sentences)</label>
                <textarea id="reasoning" placeholder="Why did you give this score? What factors influenced your decision?"></textarea>
            </div>
            <div class="form-group">
                <label>Confidence Level</label>
                <select id="confidence">
                    <option value="">Select your confidence...</option>
                    <option value="high">High - I'm very confident</option>
                    <option value="medium">Medium - Fairly confident</option>
                    <option value="low">Low - Uncertain</option>
                </select>
            </div>
            <div id="error-message" class="error hidden"></div>
            <button id="submit-btn" class="btn" onclick="submitRating()">Submit Rating</button>
        </div>
    </div>

    <!-- Result Section -->
    <div id="result-section" class="card result-card hidden">
        <h2>Thanks for your rating!</h2>
        <div class="score-comparison">
            <div class="score-box human">
                <div class="label">Your Score</div>
                <div class="value" id="result-human-score"></div>
            </div>
            <div class="score-box llm">
                <div class="label">AI Score</div>
                <div class="value" id="result-llm-score"></div>
            </div>
        </div>
        <button class="btn" onclick="loadNextPaper()">Next Paper →</button>
    </div>

    <!-- Done Section -->
    <div id="done-section" class="card done-card hidden">
        <h2>All Done!</h2>
        <p>You've rated all available papers. Thank you for your contributions!</p>
        <div class="stats" id="user-stats"></div>
        <button class="btn btn-secondary" onclick="location.reload()">Refresh to Check for New Papers</button>
    </div>

    <script>
        // State
        let apiKey = localStorage.getItem('science_agent_api_key') || '';
        let evaluator = localStorage.getItem('calibration_evaluator') || '';
        let currentItem = null;

        // Initialize inputs from localStorage
        document.getElementById('api-key-input').value = apiKey;
        document.getElementById('evaluator-input').value = evaluator;

        function getHeaders() {
            const headers = {'Content-Type': 'application/json'};
            if (apiKey) {
                headers['X-API-Key'] = apiKey;
            }
            return headers;
        }

        function updateScoreDisplay() {
            const slider = document.getElementById('score-slider');
            document.getElementById('score-display').textContent = slider.value;
        }

        function showSection(sectionId) {
            ['setup-section', 'rating-section', 'result-section', 'done-section'].forEach(id => {
                document.getElementById(id).classList.add('hidden');
            });
            document.getElementById(sectionId).classList.remove('hidden');
        }

        function showApiKeyStatus(isValid, message) {
            const statusEl = document.getElementById('api-key-status');
            statusEl.classList.remove('hidden', 'valid', 'invalid');
            statusEl.classList.add(isValid ? 'valid' : 'invalid');
            statusEl.querySelector('.status-text').textContent = message;
        }

        async function startCalibration() {
            const apiKeyInput = document.getElementById('api-key-input').value.trim();
            const evaluatorInput = document.getElementById('evaluator-input').value.trim();

            // Validate inputs
            if (!apiKeyInput) {
                showApiKeyStatus(false, 'API key is required');
                return;
            }
            if (!evaluatorInput) {
                alert('Please enter your name or email');
                return;
            }

            // Store values
            apiKey = apiKeyInput;
            evaluator = evaluatorInput;
            localStorage.setItem('science_agent_api_key', apiKey);
            localStorage.setItem('calibration_evaluator', evaluator);

            // Test API key by making a request
            showSection('rating-section');
            await loadNextPaper();
        }

        async function loadNextPaper() {
            showSection('rating-section');
            document.getElementById('loading').classList.remove('hidden');
            document.getElementById('paper-content').classList.add('hidden');
            document.getElementById('error-message').classList.add('hidden');

            try {
                const response = await fetch(`/calibration/next?evaluator=${encodeURIComponent(evaluator)}&strategy=gold_first`, {
                    headers: getHeaders()
                });

                if (response.status === 401) {
                    showSection('setup-section');
                    showApiKeyStatus(false, 'Invalid API key. Please check and try again.');
                    return;
                }

                if (response.status === 403) {
                    showSection('setup-section');
                    showApiKeyStatus(false, 'Access denied. API key does not have permission.');
                    return;
                }

                if (!response.ok) {
                    throw new Error(`Server error: ${response.status}`);
                }

                const data = await response.json();

                if (!data || !data.calibration_item_id) {
                    // No more papers
                    await showDoneSection();
                    return;
                }

                currentItem = data;
                displayPaper(data);

            } catch (error) {
                console.error('Error loading paper:', error);
                document.getElementById('loading').innerHTML =
                    '<div class="error">Error loading paper. Please check your connection and try again.</div>' +
                    '<button class="btn btn-secondary" onclick="loadNextPaper()" style="margin-top: 16px;">Retry</button>';
            }
        }

        function displayPaper(item) {
            document.getElementById('loading').classList.add('hidden');
            document.getElementById('paper-content').classList.remove('hidden');

            document.getElementById('paper-title').textContent = item.title || 'Untitled';
            document.getElementById('paper-source').textContent = item.source ? `📚 ${item.source}` : '';
            document.getElementById('paper-date').textContent = item.published_date ? `📅 ${item.published_date.split('T')[0]}` : '';
            document.getElementById('paper-summary').textContent = item.final_summary || 'No summary available.';

            // Show gold badge if applicable
            const goldBadge = document.getElementById('gold-badge');
            if (item.tags && item.tags.gold) {
                goldBadge.classList.remove('hidden');
            } else {
                goldBadge.classList.add('hidden');
            }

            // Reset form
            document.getElementById('score-slider').value = 50;
            document.getElementById('score-display').textContent = '50';
            document.getElementById('reasoning').value = '';
            document.getElementById('confidence').value = '';
            document.getElementById('submit-btn').disabled = false;
        }

        async function submitRating() {
            const score = parseInt(document.getElementById('score-slider').value);
            const reasoning = document.getElementById('reasoning').value.trim();
            const confidence = document.getElementById('confidence').value;

            if (!reasoning) {
                showError('Please provide your reasoning');
                return;
            }

            document.getElementById('submit-btn').disabled = true;
            document.getElementById('error-message').classList.add('hidden');

            try {
                const response = await fetch('/calibration/submit', {
                    method: 'POST',
                    headers: getHeaders(),
                    body: JSON.stringify({
                        calibration_item_id: currentItem.calibration_item_id,
                        evaluator: evaluator,
                        human_score: score,
                        reasoning: reasoning,
                        confidence: confidence || null
                    })
                });

                if (response.status === 401 || response.status === 403) {
                    showSection('setup-section');
                    showApiKeyStatus(false, 'Session expired. Please enter your API key again.');
                    return;
                }

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to submit');
                }

                const result = await response.json();
                showResult(score, result.llm_score);

            } catch (error) {
                console.error('Error submitting:', error);
                showError(error.message);
                document.getElementById('submit-btn').disabled = false;
            }
        }

        function showError(message) {
            const errorEl = document.getElementById('error-message');
            errorEl.textContent = message;
            errorEl.classList.remove('hidden');
        }

        function showResult(humanScore, llmScore) {
            document.getElementById('result-human-score').textContent = humanScore;
            document.getElementById('result-llm-score').textContent = llmScore !== null ? Math.round(llmScore) : 'N/A';

            // Add mismatch class if scores differ significantly
            const resultSection = document.getElementById('result-section');
            if (llmScore !== null && Math.abs(humanScore - llmScore) > 20) {
                resultSection.classList.add('mismatch');
            } else {
                resultSection.classList.remove('mismatch');
            }

            showSection('result-section');
        }

        async function showDoneSection() {
            showSection('done-section');

            try {
                const response = await fetch(`/calibration/stats?evaluator=${encodeURIComponent(evaluator)}`, {
                    headers: getHeaders()
                });

                if (!response.ok) {
                    throw new Error('Failed to load stats');
                }

                const stats = await response.json();

                document.getElementById('user-stats').innerHTML = `
                    <div class="stat-box">
                        <div class="value">${stats.total_rated || 0}</div>
                        <div class="label">Papers Rated</div>
                    </div>
                    <div class="stat-box">
                        <div class="value">${stats.avg_score !== null ? stats.avg_score : 'N/A'}</div>
                        <div class="label">Avg Score</div>
                    </div>
                    <div class="stat-box">
                        <div class="value">${stats.gold_rated || 0}/${stats.gold_total || 0}</div>
                        <div class="label">Gold Rated</div>
                    </div>
                `;
            } catch (error) {
                console.error('Error loading stats:', error);
                document.getElementById('user-stats').innerHTML = '<p>Unable to load statistics.</p>';
            }
        }

        // Auto-start if we have saved credentials
        if (apiKey && evaluator) {
            startCalibration();
        }
    </script>
</body>
</html>
'''


@router.get("", response_class=HTMLResponse)
async def calibration_ui():
    """
    Serve the calibration UI HTML page.
    """
    return HTMLResponse(content=CALIBRATION_UI_HTML)
