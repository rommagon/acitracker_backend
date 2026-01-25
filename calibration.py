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
    <title>AciTracker Calibration Tool</title>
    <style>
        * {
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
            color: #333;
        }
        .card {
            background: white;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        h1 {
            color: #2563eb;
            margin-bottom: 8px;
        }
        .subtitle {
            color: #666;
            margin-bottom: 24px;
        }
        .setup-form {
            display: flex;
            gap: 12px;
            align-items: center;
        }
        .setup-form input {
            flex: 1;
            padding: 12px;
            border: 2px solid #e5e7eb;
            border-radius: 8px;
            font-size: 16px;
        }
        .setup-form input:focus {
            outline: none;
            border-color: #2563eb;
        }
        button {
            background: #2563eb;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 8px;
            font-size: 16px;
            cursor: pointer;
            transition: background 0.2s;
        }
        button:hover {
            background: #1d4ed8;
        }
        button:disabled {
            background: #9ca3af;
            cursor: not-allowed;
        }
        .paper-title {
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 12px;
            line-height: 1.4;
        }
        .paper-meta {
            color: #666;
            font-size: 14px;
            margin-bottom: 16px;
        }
        .paper-meta span {
            margin-right: 16px;
        }
        .divider {
            border-top: 1px solid #e5e7eb;
            margin: 20px 0;
        }
        .summary-section h3 {
            font-size: 14px;
            text-transform: uppercase;
            color: #666;
            margin-bottom: 8px;
        }
        .summary-text {
            line-height: 1.6;
            color: #444;
        }
        .rating-section {
            margin-top: 24px;
        }
        .rating-section label {
            display: block;
            font-weight: 500;
            margin-bottom: 8px;
        }
        .slider-container {
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 20px;
        }
        .slider-container input[type="range"] {
            flex: 1;
            height: 8px;
            -webkit-appearance: none;
            background: linear-gradient(to right, #ef4444 0%, #f59e0b 50%, #22c55e 100%);
            border-radius: 4px;
        }
        .slider-container input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 24px;
            height: 24px;
            background: white;
            border: 2px solid #2563eb;
            border-radius: 50%;
            cursor: pointer;
        }
        .score-display {
            font-size: 24px;
            font-weight: 700;
            color: #2563eb;
            min-width: 60px;
            text-align: center;
        }
        textarea {
            width: 100%;
            padding: 12px;
            border: 2px solid #e5e7eb;
            border-radius: 8px;
            font-size: 16px;
            font-family: inherit;
            resize: vertical;
            min-height: 100px;
        }
        textarea:focus {
            outline: none;
            border-color: #2563eb;
        }
        select {
            padding: 12px;
            border: 2px solid #e5e7eb;
            border-radius: 8px;
            font-size: 16px;
            background: white;
            min-width: 150px;
        }
        select:focus {
            outline: none;
            border-color: #2563eb;
        }
        .form-row {
            margin-bottom: 20px;
        }
        .result-card {
            background: #f0fdf4;
            border: 2px solid #22c55e;
        }
        .result-card.mismatch {
            background: #fef3c7;
            border-color: #f59e0b;
        }
        .score-comparison {
            display: flex;
            gap: 40px;
            margin: 20px 0;
        }
        .score-box {
            text-align: center;
        }
        .score-box .label {
            font-size: 14px;
            color: #666;
            margin-bottom: 4px;
        }
        .score-box .value {
            font-size: 32px;
            font-weight: 700;
        }
        .score-box.human .value {
            color: #2563eb;
        }
        .score-box.llm .value {
            color: #7c3aed;
        }
        .hidden {
            display: none;
        }
        .loading {
            text-align: center;
            padding: 40px;
            color: #666;
        }
        .done-card {
            text-align: center;
            padding: 40px;
        }
        .done-card h2 {
            color: #22c55e;
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 16px;
            margin-top: 20px;
        }
        .stat-box {
            text-align: center;
            padding: 16px;
            background: #f9fafb;
            border-radius: 8px;
        }
        .stat-box .value {
            font-size: 24px;
            font-weight: 700;
            color: #2563eb;
        }
        .stat-box .label {
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
        }
        .gold-badge {
            background: #fef3c7;
            color: #92400e;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }
        .error {
            color: #dc2626;
            padding: 12px;
            background: #fef2f2;
            border-radius: 8px;
            margin-bottom: 16px;
        }
    </style>
</head>
<body>
    <div class="card">
        <h1>AciTracker Calibration</h1>
        <p class="subtitle">Help calibrate our AI relevancy scoring by rating publications</p>
    </div>

    <!-- Setup Section -->
    <div id="setup-section" class="card">
        <h2>Welcome!</h2>
        <p>Enter your name or email to get started:</p>
        <div class="setup-form">
            <input type="text" id="evaluator-input" placeholder="Your name or email">
            <button onclick="startCalibration()">Start Rating</button>
        </div>
    </div>

    <!-- Rating Section -->
    <div id="rating-section" class="card hidden">
        <div id="loading" class="loading">Loading next paper...</div>
        <div id="paper-content" class="hidden">
            <div class="paper-title" id="paper-title"></div>
            <div class="paper-meta">
                <span id="paper-source"></span>
                <span id="paper-date"></span>
                <span id="gold-badge" class="gold-badge hidden">⭐ Gold Standard</span>
            </div>
            <div class="divider"></div>
            <div class="summary-section">
                <h3>AI Summary</h3>
                <p class="summary-text" id="paper-summary"></p>
            </div>
            <div class="divider"></div>
            <div class="rating-section">
                <div class="form-row">
                    <label>Relevancy Score (0-100)</label>
                    <div class="slider-container">
                        <input type="range" id="score-slider" min="0" max="100" value="50" oninput="updateScoreDisplay()">
                        <div class="score-display" id="score-display">50</div>
                    </div>
                </div>
                <div class="form-row">
                    <label>Reasoning (1-3 sentences)</label>
                    <textarea id="reasoning" placeholder="Why did you give this score?"></textarea>
                </div>
                <div class="form-row">
                    <label>Confidence</label>
                    <select id="confidence">
                        <option value="">Select...</option>
                        <option value="high">High - I'm very sure</option>
                        <option value="medium">Medium - Fairly confident</option>
                        <option value="low">Low - Uncertain</option>
                    </select>
                </div>
                <div id="error-message" class="error hidden"></div>
                <button id="submit-btn" onclick="submitRating()">Submit Rating</button>
            </div>
        </div>
    </div>

    <!-- Result Section -->
    <div id="result-section" class="card hidden">
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
        <button onclick="loadNextPaper()">Next Paper →</button>
    </div>

    <!-- Done Section -->
    <div id="done-section" class="card done-card hidden">
        <h2>🎉 All Done!</h2>
        <p>You've rated all available papers. Thank you!</p>
        <div class="stats" id="user-stats"></div>
        <button onclick="location.reload()" style="margin-top: 20px;">Refresh to Check for New Papers</button>
    </div>

    <script>
        const API_KEY = localStorage.getItem('acitrack_api_key') || '';
        let evaluator = localStorage.getItem('calibration_evaluator') || '';
        let currentItem = null;

        function getHeaders() {
            const headers = {'Content-Type': 'application/json'};
            if (API_KEY) headers['X-API-Key'] = API_KEY;
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

        function startCalibration() {
            const input = document.getElementById('evaluator-input');
            evaluator = input.value.trim();
            if (!evaluator) {
                alert('Please enter your name or email');
                return;
            }
            localStorage.setItem('calibration_evaluator', evaluator);
            showSection('rating-section');
            loadNextPaper();
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

                if (!response.ok) {
                    throw new Error('Failed to load paper');
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
                    '<p class="error">Error loading paper. Please refresh and try again.</p>';
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
                const stats = await response.json();

                document.getElementById('user-stats').innerHTML = `
                    <div class="stat-box">
                        <div class="value">${stats.total_rated}</div>
                        <div class="label">Papers Rated</div>
                    </div>
                    <div class="stat-box">
                        <div class="value">${stats.avg_score || 'N/A'}</div>
                        <div class="label">Avg Score</div>
                    </div>
                    <div class="stat-box">
                        <div class="value">${stats.gold_rated}/${stats.gold_total}</div>
                        <div class="label">Gold Rated</div>
                    </div>
                `;
            } catch (error) {
                console.error('Error loading stats:', error);
            }
        }

        // Initialize
        if (evaluator) {
            document.getElementById('evaluator-input').value = evaluator;
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
