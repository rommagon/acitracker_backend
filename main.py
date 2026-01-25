"""
AciTrack Backend API - Postgres Edition
A FastAPI service exposing academic publication data from Render Postgres.
Replaces Google Drive backend with direct database access.
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Response, Query, Depends, Security, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_, or_, text

from db import (
    get_db,
    test_connection,
    init_db,
    engine,
    Run,
    TriModelEvent,
    MustRead,
    Publication,
    PublicationEmbedding,
    EMBEDDING_DIMENSION,
    PGVECTOR_AVAILABLE,
)
from embeddings import (
    get_openai_client,
    build_embedding_text,
    generate_embedding,
    generate_embeddings_batch,
    is_embedding_available,
    EmbeddingError,
    EMBEDDING_MODEL,
)
from calibration import router as calibration_router

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="AciTrack API",
    description="API for accessing academic publication data from Postgres database with semantic search",
    version="2.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Database initialization on startup
@app.on_event("startup")
async def startup_event():
    """
    Initialize database tables on application startup.
    Ensures all required tables exist before handling requests.
    """
    try:
        logger.info("Initializing database tables...")
        init_db()
        logger.info("Database tables initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database tables: {e}")
        logger.error("Application may not function correctly without database tables")
        # Don't raise - allow app to start so health endpoint can report the issue

# Add CORS middleware for Custom GPT access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com", "https://chatgpt.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Include calibration router
app.include_router(calibration_router)

# API Key configuration
ACITRACK_API_KEY = os.getenv("ACITRACK_API_KEY")
if not ACITRACK_API_KEY:
    logger.warning("ACITRACK_API_KEY not set - API key authentication disabled!")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: Optional[str] = Security(api_key_header)):
    """
    Verify API key for protected endpoints.
    If ACITRACK_API_KEY is not set, allows all requests (for development).
    """
    if not ACITRACK_API_KEY:
        # API key not configured - allow request (dev mode)
        return None

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-API-Key header"
        )

    if api_key != ACITRACK_API_KEY:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key"
        )

    return api_key


# Root endpoint
@app.get("/")
async def root():
    """
    Root endpoint with API information.
    """
    return {
        "name": "AciTrack API",
        "version": "2.1.0",
        "description": "Postgres-backed API for academic publication tracking with semantic search",
        "endpoints": {
            "health": "/health",
            "runs": {
                "latest": "/runs/latest?mode=tri-model-daily|daily|weekly",
                "by_id": "/runs/{run_id}"
            },
            "must_reads": "/must-reads?mode=...&run_id=...",
            "paper": "/paper/{publication_id}?run_id=...",
            "disagreements": "/disagreements?run_id=...&agreement=low,moderate&min_delta=20",
            "search": {
                "publications": "/search/publications?q=...&limit=20&min_relevancy=&min_credibility=&date_from=&date_to=",
                "status": "/search/status"
            },
            "ingest": {
                "run": "POST /ingest/run",
                "tri_model_events": "POST /ingest/tri-model-events",
                "must_reads": "POST /ingest/must-reads",
                "embeddings": "POST /ingest/embeddings"
            }
        },
        "docs": "/docs"
    }


# Health check endpoint
@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """
    Health check endpoint to verify service and database connectivity.
    """
    db_connected = False
    db_host = None
    error = None

    try:
        # Test database connection
        db.execute(text("SELECT 1"))
        db_connected = True

        # Extract host from DATABASE_URL (for display)
        database_url = os.getenv("DATABASE_URL", "")
        if "@" in database_url:
            # Format: postgresql://user:pass@host:port/db
            db_host = database_url.split("@")[1].split("/")[0]

    except Exception as e:
        logger.error(f"Health check database error: {e}")
        error = str(e)

    return {
        "status": "healthy" if db_connected else "unhealthy",
        "db_connected": db_connected,
        "db_host": db_host,
        "time": datetime.utcnow().isoformat(),
        "error": error
    }


# GET /runs/latest
@app.get("/runs/latest")
async def get_latest_run(
    mode: str = Query(default="tri-model-daily", description="Run mode"),
    db: Session = Depends(get_db),
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    Get the latest run for a specific mode.

    Query Parameters:
    - mode: "tri-model-daily", "daily", or "weekly" (default: tri-model-daily)

    Returns:
        Run metadata including run_id, mode, timestamps, and JSON fields
    """
    run = db.query(Run).filter(
        Run.mode == mode
    ).order_by(
        desc(Run.started_at)
    ).first()

    if not run:
        raise HTTPException(
            status_code=404,
            detail=f"No runs found for mode={mode}"
        )

    return {
        "run_id": run.run_id,
        "mode": run.mode,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "window_start": run.window_start.isoformat() if run.window_start else None,
        "window_end": run.window_end.isoformat() if run.window_end else None,
        "counts": json.loads(run.counts_json) if run.counts_json else None,
        "config": json.loads(run.config_json) if run.config_json else None,
        "artifacts": json.loads(run.artifacts_json) if run.artifacts_json else None,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat()
    }


# GET /runs/{run_id}
@app.get("/runs/{run_id}")
async def get_run_by_id(
    run_id: str,
    db: Session = Depends(get_db),
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    Get a specific run by run_id.

    Path Parameters:
    - run_id: Unique run identifier

    Returns:
        Run metadata including mode, timestamps, and JSON fields
    """
    run = db.query(Run).filter(Run.run_id == run_id).first()

    if not run:
        raise HTTPException(
            status_code=404,
            detail=f"Run not found: {run_id}"
        )

    return {
        "run_id": run.run_id,
        "mode": run.mode,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "window_start": run.window_start.isoformat() if run.window_start else None,
        "window_end": run.window_end.isoformat() if run.window_end else None,
        "counts": json.loads(run.counts_json) if run.counts_json else None,
        "config": json.loads(run.config_json) if run.config_json else None,
        "artifacts": json.loads(run.artifacts_json) if run.artifacts_json else None,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat()
    }


# GET /must-reads
@app.get("/must-reads")
async def get_must_reads(
    mode: Optional[str] = Query(default=None, description="Run mode to get latest must-reads"),
    run_id: Optional[str] = Query(default=None, description="Specific run_id to get must-reads"),
    db: Session = Depends(get_db),
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    Get must-read publications.

    Query Parameters:
    - mode: Get must-reads for latest run of this mode (e.g., "tri-model-daily")
    - run_id: Get must-reads for specific run_id

    One of mode or run_id is required.

    Returns:
        Must-reads JSON array
    """
    if not mode and not run_id:
        raise HTTPException(
            status_code=400,
            detail="Either 'mode' or 'run_id' query parameter is required"
        )

    # Resolve run_id from mode if needed
    if mode and not run_id:
        run = db.query(Run).filter(
            Run.mode == mode
        ).order_by(
            desc(Run.started_at)
        ).first()

        if not run:
            raise HTTPException(
                status_code=404,
                detail=f"No runs found for mode={mode}"
            )

        run_id = run.run_id

    # Get must-reads for run_id
    must_read = db.query(MustRead).filter(MustRead.run_id == run_id).first()

    if not must_read:
        raise HTTPException(
            status_code=404,
            detail=f"No must-reads found for run_id={run_id}"
        )

    # Parse and return JSON
    try:
        must_reads_data = json.loads(must_read.must_reads_json)
        return {
            "run_id": must_read.run_id,
            "mode": must_read.mode,
            "must_reads": must_reads_data,
            "created_at": must_read.created_at.isoformat(),
            "updated_at": must_read.updated_at.isoformat()
        }
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid JSON in must_reads_json: {e}"
        )


# GET /paper/{publication_id}
@app.get("/paper/{publication_id}")
async def get_paper(
    publication_id: str,
    run_id: Optional[str] = Query(default=None, description="Specific run_id to query"),
    db: Session = Depends(get_db),
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    Get tri-model event data for a specific publication.

    Path Parameters:
    - publication_id: Publication identifier

    Query Parameters:
    - run_id: Optional run_id filter (if not provided, returns latest)

    Returns:
        Tri-model event data with parsed JSON fields
    """
    query = db.query(TriModelEvent).filter(
        TriModelEvent.publication_id == publication_id
    )

    if run_id:
        query = query.filter(TriModelEvent.run_id == run_id)

    # Get latest event for this publication
    event = query.order_by(desc(TriModelEvent.created_at)).first()

    if not event:
        raise HTTPException(
            status_code=404,
            detail=f"No tri-model event found for publication_id={publication_id}"
        )

    # Parse JSON fields
    def safe_json_parse(json_str):
        if not json_str:
            return None
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    return {
        "id": event.id,
        "run_id": event.run_id,
        "mode": event.mode,
        "publication_id": event.publication_id,
        "title": event.title,
        "agreement_level": event.agreement_level,
        "disagreements": event.disagreements,
        "evaluator_rationale": event.evaluator_rationale,
        "claude_review": safe_json_parse(event.claude_review_json),
        "gemini_review": safe_json_parse(event.gemini_review_json),
        "gpt_eval": safe_json_parse(event.gpt_eval_json),
        "final_relevancy_score": event.final_relevancy_score,
        "created_at": event.created_at.isoformat()
    }


# GET /disagreements
@app.get("/disagreements")
async def get_disagreements(
    run_id: Optional[str] = Query(default=None, description="Filter by run_id"),
    agreement: Optional[str] = Query(default=None, description="Comma-separated agreement levels (e.g., 'low,moderate')"),
    min_delta: Optional[float] = Query(default=None, description="Minimum score delta between models"),
    limit: int = Query(default=50, ge=1, le=500, description="Maximum results to return"),
    db: Session = Depends(get_db),
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    Get disagreements from tri-model evaluations with filtering.

    Query Parameters:
    - run_id: Filter by specific run_id
    - agreement: Filter by agreement level (comma-separated: "low,moderate,high")
    - min_delta: Minimum score delta between Claude and Gemini (if scores available)
    - limit: Maximum number of results (default 50, max 500)

    Returns:
        List of tri-model events with disagreements
    """
    query = db.query(TriModelEvent)

    # Filter by run_id
    if run_id:
        query = query.filter(TriModelEvent.run_id == run_id)

    # Filter by agreement level
    if agreement:
        levels = [level.strip() for level in agreement.split(",")]
        query = query.filter(TriModelEvent.agreement_level.in_(levels))

    # Execute query
    events = query.order_by(desc(TriModelEvent.created_at)).limit(limit).all()

    results = []
    for event in events:
        # Parse JSON fields to compute score delta if min_delta is specified
        def safe_json_parse(json_str):
            if not json_str:
                return None
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                return None

        claude_review = safe_json_parse(event.claude_review_json)
        gemini_review = safe_json_parse(event.gemini_review_json)

        # Compute delta if both scores available
        delta = None
        claude_score = None
        gemini_score = None

        if claude_review and isinstance(claude_review, dict):
            claude_score = claude_review.get("relevancy_score") or claude_review.get("score")

        if gemini_review and isinstance(gemini_review, dict):
            gemini_score = gemini_review.get("relevancy_score") or gemini_review.get("score")

        if claude_score is not None and gemini_score is not None:
            try:
                delta = abs(float(claude_score) - float(gemini_score))
            except (ValueError, TypeError):
                delta = None

        # Apply min_delta filter
        if min_delta is not None:
            if delta is None or delta < min_delta:
                continue

        results.append({
            "id": event.id,
            "run_id": event.run_id,
            "mode": event.mode,
            "publication_id": event.publication_id,
            "title": event.title,
            "agreement_level": event.agreement_level,
            "disagreements": event.disagreements,
            "evaluator_rationale": event.evaluator_rationale,
            "claude_score": claude_score,
            "gemini_score": gemini_score,
            "score_delta": delta,
            "final_relevancy_score": event.final_relevancy_score,
            "created_at": event.created_at.isoformat()
        })

    return {
        "count": len(results),
        "filters": {
            "run_id": run_id,
            "agreement": agreement,
            "min_delta": min_delta,
            "limit": limit
        },
        "disagreements": results
    }


# POST /ingest/run
@app.post("/ingest/run")
async def ingest_run(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    Upsert a run record.

    Request Body:
    {
        "run_id": "...",
        "mode": "tri-model-daily",
        "started_at": "2025-01-24T10:00:00Z",
        "window_start": "2025-01-23T00:00:00Z",
        "window_end": "2025-01-24T00:00:00Z",
        "counts": {...},
        "config": {...},
        "artifacts": {...}
    }

    Returns:
        Created/updated run metadata
    """
    run_id = payload.get("run_id")
    if not run_id:
        raise HTTPException(status_code=400, detail="run_id is required")

    mode = payload.get("mode")
    if not mode:
        raise HTTPException(status_code=400, detail="mode is required")

    # Parse timestamps
    def parse_timestamp(ts_str):
        if not ts_str:
            return None
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    started_at = parse_timestamp(payload.get("started_at"))
    window_start = parse_timestamp(payload.get("window_start"))
    window_end = parse_timestamp(payload.get("window_end"))

    # Serialize JSON fields
    counts_json = json.dumps(payload.get("counts")) if payload.get("counts") else None
    config_json = json.dumps(payload.get("config")) if payload.get("config") else None
    artifacts_json = json.dumps(payload.get("artifacts")) if payload.get("artifacts") else None

    # Upsert run
    existing_run = db.query(Run).filter(Run.run_id == run_id).first()

    if existing_run:
        # Update
        existing_run.mode = mode
        existing_run.started_at = started_at
        existing_run.window_start = window_start
        existing_run.window_end = window_end
        existing_run.counts_json = counts_json
        existing_run.config_json = config_json
        existing_run.artifacts_json = artifacts_json
        existing_run.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing_run)
        run = existing_run
    else:
        # Insert
        run = Run(
            run_id=run_id,
            mode=mode,
            started_at=started_at,
            window_start=window_start,
            window_end=window_end,
            counts_json=counts_json,
            config_json=config_json,
            artifacts_json=artifacts_json
        )
        db.add(run)
        db.commit()
        db.refresh(run)

    return {
        "status": "success",
        "run_id": run.run_id,
        "mode": run.mode,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat()
    }


# POST /ingest/tri-model-events
@app.post("/ingest/tri-model-events")
async def ingest_tri_model_events(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    Bulk upsert tri-model events (idempotent by run_id + publication_id).

    Request Body:
    {
        "run_id": "...",
        "mode": "tri-model-daily",
        "events": [
            {
                "publication_id": "...",
                "title": "...",
                "agreement_level": "low",
                "disagreements": "...",
                "evaluator_rationale": "...",
                "claude_review": {...},
                "gemini_review": {...},
                "gpt_eval": {...},
                "final_relevancy_score": 85.5
            },
            ...
        ]
    }

    Returns:
        Summary of inserted/updated events
    """
    run_id = payload.get("run_id")
    mode = payload.get("mode")
    events = payload.get("events", [])

    if not run_id:
        raise HTTPException(status_code=400, detail="run_id is required")
    if not mode:
        raise HTTPException(status_code=400, detail="mode is required")
    if not events:
        raise HTTPException(status_code=400, detail="events array is required")

    inserted = 0
    updated = 0

    now = datetime.utcnow()

    for event_data in events:
        publication_id = event_data.get("publication_id")
        if not publication_id:
            continue

        # Serialize JSON fields
        claude_review_json = json.dumps(event_data.get("claude_review")) if event_data.get("claude_review") else None
        gemini_review_json = json.dumps(event_data.get("gemini_review")) if event_data.get("gemini_review") else None
        gpt_eval_json = json.dumps(event_data.get("gpt_eval")) if event_data.get("gpt_eval") else None

        # Check if event exists
        existing_event = db.query(TriModelEvent).filter(
            and_(
                TriModelEvent.run_id == run_id,
                TriModelEvent.publication_id == publication_id
            )
        ).first()

        if existing_event:
            # Update
            existing_event.mode = mode
            existing_event.title = event_data.get("title")
            existing_event.agreement_level = event_data.get("agreement_level")
            existing_event.disagreements = event_data.get("disagreements")
            existing_event.evaluator_rationale = event_data.get("evaluator_rationale")
            existing_event.claude_review_json = claude_review_json
            existing_event.gemini_review_json = gemini_review_json
            existing_event.gpt_eval_json = gpt_eval_json
            existing_event.final_relevancy_score = event_data.get("final_relevancy_score")
            updated += 1
        else:
            # Insert
            new_event = TriModelEvent(
                run_id=run_id,
                mode=mode,
                publication_id=publication_id,
                title=event_data.get("title"),
                agreement_level=event_data.get("agreement_level"),
                disagreements=event_data.get("disagreements"),
                evaluator_rationale=event_data.get("evaluator_rationale"),
                claude_review_json=claude_review_json,
                gemini_review_json=gemini_review_json,
                gpt_eval_json=gpt_eval_json,
                final_relevancy_score=event_data.get("final_relevancy_score")
            )
            db.add(new_event)
            inserted += 1

        # Upsert canonical publication record
        # Use direct fields from event payload if provided (not from review JSONs)
        title = event_data.get("title")
        source = event_data.get("source")  # Direct field, not from review JSON
        url = event_data.get("url")  # Direct field, not from review JSON
        relevancy_score = event_data.get("final_relevancy_score")

        # Parse published_date if provided directly in event
        published_date = None
        if event_data.get("published_date"):
            try:
                pub_date_str = event_data["published_date"]
                if isinstance(pub_date_str, str):
                    published_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                elif isinstance(pub_date_str, datetime):
                    published_date = pub_date_str
            except (ValueError, TypeError):
                pass

        # Upsert publication
        existing_pub = db.query(Publication).filter(
            Publication.publication_id == publication_id
        ).first()

        if existing_pub:
            # Update with latest info (only update non-null fields)
            if title:
                existing_pub.title = title
            if source:
                existing_pub.source = source
            if published_date:
                existing_pub.published_date = published_date
            if url:
                existing_pub.url = url
            existing_pub.latest_run_id = run_id
            if relevancy_score is not None:
                existing_pub.latest_relevancy_score = relevancy_score
            existing_pub.updated_at = now
        else:
            # Insert new publication (requires title)
            if title:
                new_pub = Publication(
                    publication_id=publication_id,
                    title=title,
                    source=source,
                    published_date=published_date,
                    url=url,
                    latest_run_id=run_id,
                    latest_relevancy_score=relevancy_score,
                )
                db.add(new_pub)

    db.commit()

    return {
        "status": "success",
        "run_id": run_id,
        "mode": mode,
        "inserted": inserted,
        "updated": updated,
        "total": len(events)
    }


# POST /ingest/must-reads
@app.post("/ingest/must-reads")
async def ingest_must_reads(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    Upsert must-reads for a run.

    Request Body:
    {
        "run_id": "...",
        "mode": "tri-model-daily",
        "must_reads": [...]
    }

    Returns:
        Created/updated must-reads metadata
    """
    run_id = payload.get("run_id")
    mode = payload.get("mode")
    must_reads = payload.get("must_reads")

    if not run_id:
        raise HTTPException(status_code=400, detail="run_id is required")
    if not mode:
        raise HTTPException(status_code=400, detail="mode is required")
    if must_reads is None:
        raise HTTPException(status_code=400, detail="must_reads is required")

    # Serialize must_reads to JSON
    must_reads_json = json.dumps(must_reads)

    # Upsert must_reads
    existing = db.query(MustRead).filter(MustRead.run_id == run_id).first()

    if existing:
        # Update
        existing.mode = mode
        existing.must_reads_json = must_reads_json
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        must_read = existing
    else:
        # Insert
        must_read = MustRead(
            run_id=run_id,
            mode=mode,
            must_reads_json=must_reads_json
        )
        db.add(must_read)
        db.commit()
        db.refresh(must_read)

    return {
        "status": "success",
        "run_id": must_read.run_id,
        "mode": must_read.mode,
        "created_at": must_read.created_at.isoformat(),
        "updated_at": must_read.updated_at.isoformat()
    }


# POST /ingest/embeddings - Generate embeddings for a run's publications
@app.post("/ingest/embeddings")
async def ingest_embeddings(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    """
    Generate embeddings for publications from a specific run.

    This endpoint is designed to be called after /ingest/tri-model-events
    to generate embeddings for new publications.

    Request Body:
    {
        "run_id": "...",         # Required: run_id to process
        "limit": 100,            # Optional: max publications to process (default: all)
        "force_regenerate": false # Optional: regenerate existing embeddings
    }

    Returns:
        Summary of embedding generation results
    """
    run_id = payload.get("run_id")
    limit = payload.get("limit")
    force_regenerate = payload.get("force_regenerate", False)

    if not run_id:
        raise HTTPException(status_code=400, detail="run_id is required")

    if not is_embedding_available():
        raise HTTPException(
            status_code=503,
            detail="Embedding generation unavailable - SPOTITEARLY_LLM_API_KEY not configured"
        )

    if not PGVECTOR_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="pgvector extension not available"
        )

    # Get publications from this run that need embeddings
    query = db.query(TriModelEvent).filter(
        TriModelEvent.run_id == run_id,
        TriModelEvent.title.isnot(None),
        TriModelEvent.title != ""
    )

    if not force_regenerate:
        # Only get publications without embeddings
        existing_ids = db.query(PublicationEmbedding.publication_id).filter(
            PublicationEmbedding.embedding.isnot(None)
        ).subquery()
        query = query.filter(~TriModelEvent.publication_id.in_(existing_ids))

    if limit:
        query = query.limit(limit)

    events = query.all()

    if not events:
        return {
            "status": "success",
            "run_id": run_id,
            "message": "No publications need embeddings",
            "processed": 0,
            "success": 0,
            "errors": 0
        }

    # Get OpenAI client
    client = get_openai_client()
    if not client:
        raise HTTPException(
            status_code=503,
            detail="Could not initialize OpenAI client"
        )

    # Process publications
    success_count = 0
    error_count = 0
    now = datetime.utcnow()

    # Build texts for batch embedding
    # Get metadata from canonical publications table (not from review JSONs)
    items = []
    for event in events:
        try:
            # Look up canonical publication for metadata
            pub = db.query(Publication).filter(
                Publication.publication_id == event.publication_id
            ).first()

            # Use metadata from publications table if available
            source = pub.source if pub else None
            published_date = pub.published_date if pub else None

            text = build_embedding_text(
                title=event.title,
                source=source,
                evaluator_rationale=event.evaluator_rationale,
            )
            items.append({
                "publication_id": event.publication_id,
                "title": event.title,
                "text": text,
                "source": source,
                "published_date": published_date,
                "final_relevancy_score": event.final_relevancy_score,
            })
        except ValueError as e:
            logger.warning(f"Skipping {event.publication_id}: {e}")
            error_count += 1

    if not items:
        return {
            "status": "success",
            "run_id": run_id,
            "message": "No valid publications to embed",
            "processed": len(events),
            "success": 0,
            "errors": error_count
        }

    # Generate embeddings in batch (process in chunks of 50)
    batch_size = 50
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        texts = [item["text"] for item in batch]

        try:
            embeddings = generate_embeddings_batch(texts, client)

            for j, item in enumerate(batch):
                try:
                    embedding = embeddings[j]

                    # Upsert PublicationEmbedding
                    existing = db.query(PublicationEmbedding).filter(
                        PublicationEmbedding.publication_id == item["publication_id"]
                    ).first()

                    if existing:
                        existing.title = item["title"]
                        existing.source = item.get("source")
                        existing.published_date = item.get("published_date")
                        existing.embedded_text = item["text"]
                        existing.embedding = embedding
                        existing.embedding_model = EMBEDDING_MODEL
                        existing.embedded_at = now
                        existing.latest_run_id = run_id
                        existing.final_relevancy_score = item.get("final_relevancy_score")
                        existing.updated_at = now
                    else:
                        new_embedding = PublicationEmbedding(
                            publication_id=item["publication_id"],
                            title=item["title"],
                            source=item.get("source"),
                            published_date=item.get("published_date"),
                            embedded_text=item["text"],
                            embedding=embedding,
                            embedding_model=EMBEDDING_MODEL,
                            embedded_at=now,
                            latest_run_id=run_id,
                            final_relevancy_score=item.get("final_relevancy_score"),
                        )
                        db.add(new_embedding)

                    success_count += 1

                except Exception as e:
                    logger.error(f"Error storing embedding for {item['publication_id']}: {e}")
                    error_count += 1

            db.commit()

        except EmbeddingError as e:
            logger.error(f"Batch embedding failed: {e}")
            error_count += len(batch)

    return {
        "status": "success",
        "run_id": run_id,
        "processed": len(events),
        "success": success_count,
        "errors": error_count
    }


# GET /search/publications - Semantic search across all publications
@app.get("/search/publications")
async def search_publications(
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    limit: int = Query(default=20, ge=1, le=100, description="Maximum results to return"),
    min_relevancy: Optional[float] = Query(default=None, ge=0, le=100, description="Minimum relevancy score filter"),
    min_credibility: Optional[float] = Query(default=None, ge=0, le=100, description="Minimum credibility score filter"),
    date_from: Optional[str] = Query(default=None, description="Filter publications from date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(default=None, description="Filter publications to date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    Semantic search across all publications using pgvector similarity.

    Use this endpoint for conceptual queries like "dogs sniffing cancer" to find
    relevant publications across the entire database history.

    Query Parameters:
    - q: Search query (required, 1-500 characters)
    - limit: Maximum results to return (default 20, max 100)
    - min_relevancy: Filter out results below this relevancy score (0-100)
    - min_credibility: Filter out results below this credibility score (0-100)
    - date_from: Only include publications from this date (YYYY-MM-DD)
    - date_to: Only include publications up to this date (YYYY-MM-DD)

    Returns:
        Ranked semantic search results with publication details
    """
    if not PGVECTOR_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Semantic search unavailable - pgvector extension not installed"
        )

    if not is_embedding_available():
        raise HTTPException(
            status_code=503,
            detail="Semantic search unavailable - SPOTITEARLY_LLM_API_KEY not configured"
        )

    # Check if we have any embeddings
    embedding_count = db.query(PublicationEmbedding).filter(
        PublicationEmbedding.embedding.isnot(None)
    ).count()

    if embedding_count == 0:
        raise HTTPException(
            status_code=503,
            detail="No publication embeddings available. Run backfill script first: python scripts/backfill_embeddings.py"
        )

    # Generate query embedding
    try:
        client = get_openai_client()
        query_embedding = generate_embedding(q, client)
    except EmbeddingError as e:
        logger.error(f"Failed to generate query embedding: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to process search query"
        )

    # Build the search query using pgvector
    # We use the <-> operator for L2 distance (cosine similarity would be <=>)
    # Lower distance = more similar

    # Convert embedding to string format for SQL
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    # Build filters
    where_clauses = ["embedding IS NOT NULL"]
    params = {"query_embedding": embedding_str, "limit": limit}

    if min_relevancy is not None:
        where_clauses.append("final_relevancy_score >= :min_relevancy")
        params["min_relevancy"] = min_relevancy

    if min_credibility is not None:
        where_clauses.append("credibility_score >= :min_credibility")
        params["min_credibility"] = min_credibility

    if date_from:
        try:
            datetime.strptime(date_from, "%Y-%m-%d")
            where_clauses.append("published_date >= :date_from")
            params["date_from"] = date_from
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_from format. Use YYYY-MM-DD")

    if date_to:
        try:
            datetime.strptime(date_to, "%Y-%m-%d")
            where_clauses.append("published_date <= :date_to")
            params["date_to"] = date_to
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_to format. Use YYYY-MM-DD")

    where_sql = " AND ".join(where_clauses)

    # Execute semantic search query
    # Note: Use (:param)::vector syntax to avoid SQLAlchemy parsing issues with ::
    # JOIN with canonical publications table for reliable metadata (source, published_date)
    # Use LEFT JOIN LATERAL on tri_model_events for latest_run_id and scores
    search_query = text(f"""
        WITH ranked_results AS (
            SELECT
                pe.publication_id,
                pe.embedding <-> (:query_embedding)::vector AS distance
            FROM publication_embeddings pe
            WHERE {where_sql}
            ORDER BY pe.embedding <-> (:query_embedding)::vector
            LIMIT :limit
        )
        SELECT
            rr.publication_id,
            p.title,
            p.source,
            p.published_date,
            p.latest_run_id,
            COALESCE(p.latest_relevancy_score, tme.final_relevancy_score) AS final_relevancy_score,
            p.latest_credibility_score AS credibility_score,
            pe.final_summary,
            rr.distance
        FROM ranked_results rr
        JOIN publications p ON p.publication_id = rr.publication_id
        LEFT JOIN publication_embeddings pe ON pe.publication_id = rr.publication_id
        LEFT JOIN LATERAL (
            SELECT final_relevancy_score
            FROM tri_model_events
            WHERE publication_id = rr.publication_id
            ORDER BY created_at DESC
            LIMIT 1
        ) tme ON true
        ORDER BY rr.distance
    """)

    try:
        result = db.execute(search_query, params)
        rows = result.fetchall()
    except Exception as e:
        logger.error(f"Search query failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="Search failed"
        )

    # Format results
    results = []
    for row in rows:
        # Convert distance to similarity score (0-1, higher is better)
        distance = float(row[8]) if row[8] is not None else 0
        # Approximate similarity: 1 / (1 + distance) or normalize differently
        similarity = 1 / (1 + distance) if distance >= 0 else 0

        results.append({
            "publication_id": row[0],
            "title": row[1],
            "source": row[2],
            "published_date": row[3].isoformat() if row[3] else None,
            "best_run_id": row[4],
            "final_relevancy_score": row[5],
            "credibility_score": row[6],
            "final_summary": row[7][:500] if row[7] else None,  # Limit summary length
            "similarity": round(similarity, 4),
        })

    return {
        "query": q,
        "count": len(results),
        "filters": {
            "min_relevancy": min_relevancy,
            "min_credibility": min_credibility,
            "date_from": date_from,
            "date_to": date_to,
        },
        "results": results
    }


# GET /search/status - Check semantic search availability
@app.get("/search/status")
async def search_status(
    db: Session = Depends(get_db),
    api_key: Optional[str] = Depends(verify_api_key)
):
    """
    Check the status of semantic search functionality.

    Returns information about:
    - pgvector extension availability
    - OpenAI API configuration
    - Number of publications with embeddings
    """
    embedding_count = 0
    total_publications = 0

    try:
        embedding_count = db.query(PublicationEmbedding).filter(
            PublicationEmbedding.embedding.isnot(None)
        ).count()
        total_publications = db.query(TriModelEvent.publication_id).distinct().count()
    except Exception as e:
        logger.warning(f"Could not count embeddings: {e}")

    return {
        "pgvector_available": PGVECTOR_AVAILABLE,
        "openai_configured": is_embedding_available(),
        "embedding_model": EMBEDDING_MODEL if is_embedding_available() else None,
        "publications_with_embeddings": embedding_count,
        "total_unique_publications": total_publications,
        "coverage_percent": round(100 * embedding_count / total_publications, 1) if total_publications > 0 else 0,
        "search_available": PGVECTOR_AVAILABLE and is_embedding_available() and embedding_count > 0,
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
