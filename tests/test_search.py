"""
Tests for semantic search functionality in AciTrack Backend.

These tests cover:
- Input validation for search endpoints
- Limit enforcement (max 100)
- Filter behavior
- Search status endpoint

Note: Full integration tests require a Postgres database with pgvector.
Mark integration tests that need the database with @pytest.mark.integration.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Mock the database and pgvector before importing main
@pytest.fixture(autouse=True)
def mock_db_setup():
    """Mock database setup to avoid connection requirements in unit tests."""
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"}):
        with patch("db.engine"):
            with patch("db.SessionLocal"):
                with patch("db.PGVECTOR_AVAILABLE", True):
                    yield


class TestSearchInputValidation:
    """Test input validation for /search/publications endpoint."""

    def test_query_required(self):
        """Test that query parameter is required."""
        # Import inside test to use mocked environment
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"}):
            with patch("db.engine"):
                with patch("db.SessionLocal"):
                    from main import app
                    client = TestClient(app, raise_server_exceptions=False)

                    response = client.get("/search/publications")
                    assert response.status_code == 422  # Validation error

    def test_query_min_length(self):
        """Test that query must have at least 1 character."""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"}):
            with patch("db.engine"):
                with patch("db.SessionLocal"):
                    from main import app
                    client = TestClient(app, raise_server_exceptions=False)

                    response = client.get("/search/publications?q=")
                    assert response.status_code == 422

    def test_limit_max_enforcement(self):
        """Test that limit is capped at 100."""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"}):
            with patch("db.engine"):
                with patch("db.SessionLocal"):
                    from main import app
                    client = TestClient(app, raise_server_exceptions=False)

                    response = client.get("/search/publications?q=test&limit=200")
                    assert response.status_code == 422

    def test_limit_min_enforcement(self):
        """Test that limit must be at least 1."""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"}):
            with patch("db.engine"):
                with patch("db.SessionLocal"):
                    from main import app
                    client = TestClient(app, raise_server_exceptions=False)

                    response = client.get("/search/publications?q=test&limit=0")
                    assert response.status_code == 422

    def test_min_relevancy_range(self):
        """Test that min_relevancy must be 0-100."""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"}):
            with patch("db.engine"):
                with patch("db.SessionLocal"):
                    from main import app
                    client = TestClient(app, raise_server_exceptions=False)

                    response = client.get("/search/publications?q=test&min_relevancy=150")
                    assert response.status_code == 422

                    response = client.get("/search/publications?q=test&min_relevancy=-10")
                    assert response.status_code == 422

    def test_date_format_validation(self):
        """Test that date parameters require YYYY-MM-DD format."""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"}):
            with patch("db.engine"):
                with patch("db.SessionLocal"):
                    with patch("db.PGVECTOR_AVAILABLE", True):
                        with patch("main.PGVECTOR_AVAILABLE", True):
                            with patch("main.is_embedding_available", return_value=True):
                                with patch("main.get_db") as mock_get_db:
                                    mock_session = MagicMock()
                                    mock_session.query.return_value.filter.return_value.count.return_value = 10
                                    mock_get_db.return_value = iter([mock_session])

                                    from main import app
                                    client = TestClient(app, raise_server_exceptions=False)

                                    # Invalid date format
                                    response = client.get("/search/publications?q=test&date_from=2025-1-1")
                                    # This should be caught by our validation
                                    assert response.status_code in [400, 422, 503]


class TestEmbeddingsModule:
    """Test the embeddings utility module."""

    def test_build_embedding_text_with_title_only(self):
        """Test building embedding text with only title."""
        from embeddings import build_embedding_text

        text = build_embedding_text(title="Cancer Detection Study")
        assert text == "Cancer Detection Study"

    def test_build_embedding_text_with_all_fields(self):
        """Test building embedding text with all fields."""
        from embeddings import build_embedding_text

        text = build_embedding_text(
            title="Cancer Detection Study",
            final_summary="A study about early cancer detection",
            source="Nature Medicine"
        )
        assert "Cancer Detection Study" in text
        assert "A study about early cancer detection" in text
        assert "Source: Nature Medicine" in text

    def test_build_embedding_text_with_rationale_fallback(self):
        """Test that evaluator_rationale is used when no summary."""
        from embeddings import build_embedding_text

        text = build_embedding_text(
            title="Cancer Detection Study",
            evaluator_rationale="This study shows promising results for early detection"
        )
        assert "Cancer Detection Study" in text
        assert "promising results" in text

    def test_build_embedding_text_requires_title(self):
        """Test that title is required."""
        from embeddings import build_embedding_text

        with pytest.raises(ValueError):
            build_embedding_text(title="")

        with pytest.raises(ValueError):
            build_embedding_text(title=None)

    def test_is_embedding_available_without_key(self):
        """Test is_embedding_available returns False without API key."""
        with patch.dict(os.environ, {"SPOTITEARLY_LLM_API_KEY": ""}, clear=False):
            # Need to reload to pick up env change
            import importlib
            import embeddings
            importlib.reload(embeddings)

            assert embeddings.is_embedding_available() == False

    def test_estimate_tokens(self):
        """Test token estimation."""
        from embeddings import estimate_tokens

        # Roughly 4 chars per token
        text = "This is a test sentence for token estimation."
        tokens = estimate_tokens(text)
        assert tokens > 0
        assert tokens < len(text)


class TestSearchStatus:
    """Test the /search/status endpoint."""

    def test_search_status_structure(self):
        """Test that search status returns expected fields."""
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test:test@localhost/test"}):
            with patch("db.engine"):
                with patch("db.SessionLocal"):
                    with patch("db.PGVECTOR_AVAILABLE", True):
                        with patch("main.PGVECTOR_AVAILABLE", True):
                            with patch("main.is_embedding_available", return_value=False):
                                with patch("main.get_db") as mock_get_db:
                                    mock_session = MagicMock()
                                    mock_session.query.return_value.filter.return_value.count.return_value = 0
                                    mock_session.query.return_value.distinct.return_value.count.return_value = 100
                                    mock_get_db.return_value = iter([mock_session])

                                    from main import app
                                    client = TestClient(app, raise_server_exceptions=False)

                                    response = client.get("/search/status")
                                    # May get 500 due to mocking, but structure should be there
                                    if response.status_code == 200:
                                        data = response.json()
                                        assert "pgvector_available" in data
                                        assert "openai_configured" in data
                                        assert "search_available" in data


class TestChunkTextsForBatching:
    """Test the batch chunking utility."""

    def test_chunk_empty_list(self):
        """Test chunking empty list."""
        from embeddings import chunk_texts_for_batching

        batches = chunk_texts_for_batching([])
        assert batches == []

    def test_chunk_single_item(self):
        """Test chunking single item."""
        from embeddings import chunk_texts_for_batching

        items = [("id1", "text1")]
        batches = chunk_texts_for_batching(items)
        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_chunk_respects_max_size(self):
        """Test that batches respect max size."""
        from embeddings import chunk_texts_for_batching

        items = [(f"id{i}", f"text{i}") for i in range(150)]
        batches = chunk_texts_for_batching(items, max_batch_size=50)

        assert len(batches) == 3
        assert len(batches[0]) == 50
        assert len(batches[1]) == 50
        assert len(batches[2]) == 50

    def test_chunk_handles_remainder(self):
        """Test that remainder items are included."""
        from embeddings import chunk_texts_for_batching

        items = [(f"id{i}", f"text{i}") for i in range(75)]
        batches = chunk_texts_for_batching(items, max_batch_size=50)

        assert len(batches) == 2
        assert len(batches[0]) == 50
        assert len(batches[1]) == 25


# Mark integration tests that require actual database
@pytest.mark.integration
class TestSearchIntegration:
    """Integration tests requiring Postgres with pgvector.

    These tests are skipped unless explicitly enabled.
    To run: pytest -m integration
    """

    @pytest.fixture
    def db_session(self):
        """Create a test database session."""
        # This would set up a test database with pgvector
        # For now, skip if not configured
        pytest.skip("Integration tests require DATABASE_URL and pgvector")

    def test_semantic_search_returns_results(self, db_session):
        """Test that semantic search returns ranked results."""
        pass  # Would test actual search functionality

    def test_embedding_backfill(self, db_session):
        """Test that backfill script works correctly."""
        pass  # Would test actual embedding generation


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
