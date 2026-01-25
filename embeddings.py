"""
OpenAI Embeddings utility module for AciTrack semantic search.
Handles embedding generation with batching, rate limiting, and retries.
"""

import os
import logging
from typing import List, Optional, Tuple
from datetime import datetime

from openai import OpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

# Configure logging
logger = logging.getLogger(__name__)

# OpenAI configuration
OPENAI_API_KEY = os.getenv("SPOTITEARLY_LLM_API_KEY")
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536

# Batch configuration
MAX_BATCH_SIZE = 100  # OpenAI recommends max 2048 but we use smaller for safety
MAX_TOKENS_PER_BATCH = 8000  # Approximate token limit per batch


class EmbeddingError(Exception):
    """Custom exception for embedding generation errors."""
    pass


def get_openai_client() -> Optional[OpenAI]:
    """
    Get OpenAI client configured with the API key.
    Returns None if API key not configured.
    """
    if not OPENAI_API_KEY:
        logger.warning("SPOTITEARLY_LLM_API_KEY not set - embeddings unavailable")
        return None
    return OpenAI(api_key=OPENAI_API_KEY)


def build_embedding_text(
    title: str,
    final_summary: Optional[str] = None,
    source: Optional[str] = None,
    evaluator_rationale: Optional[str] = None,
) -> str:
    """
    Build the text to embed for a publication.
    Combines available fields into a searchable text representation.

    Priority:
    1. Title (required)
    2. Final summary (if available)
    3. Source (if available)
    4. Evaluator rationale as fallback summary

    Args:
        title: Publication title
        final_summary: Summary from evaluation (preferred)
        source: Publication source/journal
        evaluator_rationale: Fallback if no summary

    Returns:
        Combined text for embedding
    """
    if not title:
        raise ValueError("Title is required for embedding")

    parts = [title.strip()]

    # Add summary or rationale
    if final_summary and final_summary.strip():
        parts.append(final_summary.strip())
    elif evaluator_rationale and evaluator_rationale.strip():
        # Use first 500 chars of rationale as summary fallback
        rationale_preview = evaluator_rationale.strip()[:500]
        parts.append(rationale_preview)

    # Add source
    if source and source.strip():
        parts.append(f"Source: {source.strip()}")

    return " | ".join(parts)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((Exception,)),
    reraise=True,
)
def generate_embedding(text: str, client: Optional[OpenAI] = None) -> List[float]:
    """
    Generate embedding for a single text using OpenAI API.

    Args:
        text: Text to embed
        client: Optional pre-configured OpenAI client

    Returns:
        List of floats representing the embedding vector

    Raises:
        EmbeddingError: If embedding generation fails
    """
    if client is None:
        client = get_openai_client()

    if client is None:
        raise EmbeddingError("OpenAI client not available - API key not configured")

    if not text or not text.strip():
        raise EmbeddingError("Cannot generate embedding for empty text")

    try:
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text.strip(),
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Error generating embedding: {e}")
        raise EmbeddingError(f"Failed to generate embedding: {e}") from e


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((Exception,)),
    reraise=True,
)
def generate_embeddings_batch(
    texts: List[str],
    client: Optional[OpenAI] = None
) -> List[List[float]]:
    """
    Generate embeddings for a batch of texts using OpenAI API.

    Args:
        texts: List of texts to embed
        client: Optional pre-configured OpenAI client

    Returns:
        List of embedding vectors (in same order as input texts)

    Raises:
        EmbeddingError: If embedding generation fails
    """
    if client is None:
        client = get_openai_client()

    if client is None:
        raise EmbeddingError("OpenAI client not available - API key not configured")

    if not texts:
        return []

    # Filter empty texts
    cleaned_texts = [t.strip() for t in texts if t and t.strip()]
    if not cleaned_texts:
        raise EmbeddingError("All texts are empty")

    try:
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=cleaned_texts,
        )
        # Return embeddings in order
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
    except Exception as e:
        logger.error(f"Error generating batch embeddings: {e}")
        raise EmbeddingError(f"Failed to generate batch embeddings: {e}") from e


def chunk_texts_for_batching(
    items: List[Tuple[str, str]],  # List of (id, text) tuples
    max_batch_size: int = MAX_BATCH_SIZE,
) -> List[List[Tuple[str, str]]]:
    """
    Chunk items into batches for efficient API calls.

    Args:
        items: List of (id, text) tuples
        max_batch_size: Maximum items per batch

    Returns:
        List of batches, each batch is a list of (id, text) tuples
    """
    batches = []
    current_batch = []

    for item in items:
        current_batch.append(item)

        if len(current_batch) >= max_batch_size:
            batches.append(current_batch)
            current_batch = []

    if current_batch:
        batches.append(current_batch)

    return batches


def estimate_tokens(text: str) -> int:
    """
    Rough estimate of token count for a text.
    Uses simple heuristic: ~4 characters per token for English.
    """
    return len(text) // 4


def is_embedding_available() -> bool:
    """Check if embedding generation is available (API key configured)."""
    return OPENAI_API_KEY is not None and len(OPENAI_API_KEY) > 0
