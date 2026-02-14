"""
Database configuration and models for AciTrack Backend.
Uses SQLAlchemy 2.0 with sync driver (psycopg2) for Render Postgres.
Includes pgvector extension for semantic search embeddings.
"""

import os
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from sqlalchemy import (
    create_engine,
    Column,
    String,
    Integer,
    BigInteger,
    Float,
    Date,
    DateTime,
    Text,
    Index,
    text,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import NullPool

# Try to import pgvector support
try:
    from pgvector.sqlalchemy import Vector
    PGVECTOR_AVAILABLE = True
except ImportError:
    PGVECTOR_AVAILABLE = False
    Vector = None

# Read DATABASE_URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")

# Ensure SSL mode for Render Postgres
# Render requires sslmode=require for Postgres connections
def ensure_ssl_mode(url: str) -> str:
    """
    Ensure DATABASE_URL has sslmode=require for Render Postgres.
    Safe for local dev (only adds if not already present).
    """
    if "sslmode=" in url:
        # SSL mode already configured
        return url

    parsed = urlparse(url)

    if parsed.query:
        # Query string exists, append to it
        return url + "&sslmode=require"
    else:
        # No query string, add one
        return url + "?sslmode=require"

DATABASE_URL = ensure_ssl_mode(DATABASE_URL)

# Create SQLAlchemy engine (sync)
# Use NullPool for serverless environments to avoid connection pool issues
engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool,
    echo=False,  # Set to True for SQL query logging during development
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


class Run(Base):
    """
    Stores metadata for each pipeline run (daily/weekly).
    """
    __tablename__ = "runs"

    run_id = Column(String, primary_key=True, index=True)
    mode = Column(String, nullable=False, index=True)  # "tri-model-daily", "daily", "weekly"
    started_at = Column(DateTime, nullable=True)
    window_start = Column(DateTime, nullable=True)
    window_end = Column(DateTime, nullable=True)

    # JSON fields stored as text
    counts_json = Column(Text, nullable=True)  # JSON string with counts
    config_json = Column(Text, nullable=True)  # JSON string with run config
    artifacts_json = Column(Text, nullable=True)  # JSON string with artifact metadata

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Indexes
    __table_args__ = (
        Index("idx_runs_mode_started", "mode", "started_at"),
    )


class TriModelEvent(Base):
    """
    Stores tri-model evaluation events (publications evaluated by multiple models).
    Corresponds to the existing tri_model_events table in the database.
    """
    __tablename__ = "tri_model_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, nullable=False, index=True)
    mode = Column(String, nullable=False, index=True)  # "tri-model-daily", etc.
    publication_id = Column(String, nullable=False, index=True)
    title = Column(Text, nullable=True)

    # Tri-model scoring fields
    agreement_level = Column(String, nullable=True)  # "high", "moderate", "low"
    disagreements = Column(Text, nullable=True)  # Text description of disagreements
    evaluator_rationale = Column(Text, nullable=True)

    # Model review JSON (stored as text)
    claude_review_json = Column(Text, nullable=True)
    gemini_review_json = Column(Text, nullable=True)
    gpt_eval_json = Column(Text, nullable=True)

    # Final scores
    final_relevancy_score = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Composite unique constraint
    __table_args__ = (
        Index("idx_tri_model_run_pub", "run_id", "publication_id", unique=True),
        Index("idx_tri_model_mode_agreement", "mode", "agreement_level"),
    )


class MustRead(Base):
    """
    Stores must-read decisions per run.
    Each run has one row with a JSON blob of must-reads.
    """
    __tablename__ = "must_reads"

    run_id = Column(String, primary_key=True, index=True)
    mode = Column(String, nullable=False, index=True)
    must_reads_json = Column(Text, nullable=False)  # JSON array of must-read publications

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class WeeklyDigestFeedback(Base):
    """
    Stores thumbs up/down feedback clicks from weekly digest emails.
    """
    __tablename__ = "weekly_digest_feedback"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, server_default=text("NOW()"), nullable=False)
    week_start = Column(Date, nullable=False)
    week_end = Column(Date, nullable=False)
    publication_id = Column(Text, nullable=False)
    vote = Column(Text, nullable=False)
    source_ip = Column(Text, nullable=True)
    user_agent = Column(Text, nullable=True)
    context_json = Column(Text, nullable=True)


class Publication(Base):
    """
    Centralized publications table - single source of truth for all publication data.
    All scoring, credibility, and metadata live here. No joins needed.
    """
    __tablename__ = "publications"

    # ── Metadata ──
    publication_id = Column(String, primary_key=True, index=True)
    title = Column(Text, nullable=False)
    authors = Column(Text, nullable=True)  # Comma-separated
    source = Column(String, nullable=True)  # Source feed name
    venue = Column(String, nullable=True)  # Journal/venue name
    published_date = Column(String, nullable=True)  # ISO 8601 date string
    url = Column(Text, nullable=True)  # Original URL
    canonical_url = Column(Text, nullable=True)  # Resolved canonical URL
    doi = Column(String, nullable=True)
    pmid = Column(String, nullable=True)  # PubMed ID
    source_type = Column(String, nullable=True)  # pubmed, rss, biorxiv, etc.
    raw_text = Column(Text, nullable=True)  # Full abstract/text
    summary = Column(Text, nullable=True)  # Base summary

    # ── Scoring (centralized) ──
    final_relevancy_score = Column(Integer, nullable=True)  # 0-100
    final_relevancy_reason = Column(Text, nullable=True)
    final_summary = Column(Text, nullable=True)  # Tri-model synthesized summary
    claude_score = Column(Integer, nullable=True)
    gemini_score = Column(Integer, nullable=True)
    agreement_level = Column(String, nullable=True)  # high / moderate / low
    confidence = Column(String, nullable=True)
    evaluator_rationale = Column(Text, nullable=True)
    disagreements = Column(Text, nullable=True)
    final_signals_json = Column(Text, nullable=True)  # JSON blob

    # ── Credibility ──
    credibility_score = Column(Integer, nullable=True)  # 0-100
    credibility_reason = Column(Text, nullable=True)
    credibility_confidence = Column(String, nullable=True)  # low / medium / high
    credibility_signals_json = Column(Text, nullable=True)  # JSON blob

    # ── Audit ──
    scoring_run_id = Column(String, nullable=True)  # Pipeline run that scored
    scoring_updated_at = Column(DateTime, nullable=True)
    latest_run_id = Column(String, nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # ── Kept for backward compat (used by existing ingest endpoint) ──
    latest_relevancy_score = Column(Float, nullable=True)
    latest_credibility_score = Column(Float, nullable=True)


# Embedding dimension for text-embedding-3-small
EMBEDDING_DIMENSION = 1536


class PublicationEmbedding(Base):
    """
    Stores embeddings for publications to enable semantic search.
    One embedding per unique publication_id across all runs.
    Uses pgvector for efficient similarity search.
    """
    __tablename__ = "publication_embeddings"

    publication_id = Column(String, primary_key=True, index=True)

    # Publication metadata (denormalized for search results)
    title = Column(Text, nullable=True)
    source = Column(String, nullable=True)
    published_date = Column(DateTime, nullable=True)

    # Text that was embedded (for debugging/auditing)
    embedded_text = Column(Text, nullable=True)

    # Embedding vector (1536 dimensions for text-embedding-3-small)
    # Note: Vector column created conditionally if pgvector available
    embedding = Column(
        Vector(EMBEDDING_DIMENSION) if PGVECTOR_AVAILABLE else Text,
        nullable=True
    )

    # Embedding metadata
    embedding_model = Column(String, nullable=True, default="text-embedding-3-small")
    embedded_at = Column(DateTime, nullable=True)

    # Cached scores from most recent run (for filtering)
    latest_run_id = Column(String, nullable=True)
    final_relevancy_score = Column(Float, nullable=True)
    credibility_score = Column(Float, nullable=True)
    final_summary = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class CalibrationItem(Base):
    """
    Stores publications for human calibration/labeling.
    One row per unique publication to be rated.
    """
    __tablename__ = "calibration_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    publication_id = Column(String, nullable=False, unique=True, index=True)
    mode = Column(String, nullable=True)  # tri-model-daily, etc.
    run_id = Column(String, nullable=True)

    # Display metadata
    source = Column(String, nullable=True)
    published_date = Column(DateTime, nullable=True)
    title = Column(Text, nullable=True)
    abstract = Column(Text, nullable=True)

    # LLM scores for comparison
    final_relevancy_score = Column(Float, nullable=True)
    final_summary = Column(Text, nullable=True)

    # Tags for categorization (e.g., {"gold": true, "topic": "breast"})
    tags = Column(JSONB, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class HumanEvaluation(Base):
    """
    Stores human ratings for calibration items.
    One rating per evaluator per publication.
    """
    __tablename__ = "human_evaluations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    calibration_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("calibration_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    evaluator = Column(String, nullable=False, index=True)
    human_score = Column(Integer, nullable=False)  # 0-100
    reasoning = Column(Text, nullable=False)
    confidence = Column(String, nullable=True)  # low/medium/high

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("calibration_item_id", "evaluator", name="uq_calibration_evaluator"),
    )


def get_db() -> Session:
    """
    Dependency function to get a database session.
    Yields a session and closes it after use.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_pgvector_extension():
    """
    Ensure pgvector extension is enabled in the database.
    Safe to call multiple times (uses IF NOT EXISTS).
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
        return True
    except Exception as e:
        print(f"Warning: Could not enable pgvector extension: {e}")
        print("Semantic search will not be available.")
        return False


def init_db():
    """
    Initialize database tables and extensions.
    Creates pgvector extension and all tables defined in Base metadata.

    Note: For production, use Alembic migrations instead.
    """
    # First, ensure pgvector extension exists
    ensure_pgvector_extension()

    # Then create all tables
    Base.metadata.create_all(bind=engine)


def test_connection() -> bool:
    """
    Test database connectivity.

    Returns:
        True if connection successful, False otherwise
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False
