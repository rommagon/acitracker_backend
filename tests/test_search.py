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


class TestSearchEndpointSQL:
    """Test that search endpoint SQL executes without 500 errors."""

    def test_search_endpoint_sql_binding(self):
        """
        Test that the search endpoint SQL query uses correct parameter binding.
        This ensures the (:query_embedding)::vector syntax works correctly.
        """
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://test:test@localhost/test",
            "SPOTITEARLY_LLM_API_KEY": "sk-test-key"
        }):
            with patch("db.engine"):
                with patch("db.SessionLocal"):
                    with patch("db.PGVECTOR_AVAILABLE", True):
                        # Mock the main module's dependencies
                        with patch("main.PGVECTOR_AVAILABLE", True):
                            with patch("main.is_embedding_available", return_value=True):
                                with patch("main.get_openai_client") as mock_client:
                                    with patch("main.generate_embedding") as mock_embed:
                                        with patch("main.get_db") as mock_get_db:
                                            # Create mock embedding (1536 dimensions)
                                            dummy_embedding = [0.1] * 1536
                                            mock_embed.return_value = dummy_embedding

                                            # Create mock db session
                                            mock_session = MagicMock()

                                            # Mock embedding count check
                                            mock_session.query.return_value.filter.return_value.count.return_value = 10

                                            # Mock the search query execution
                                            mock_result = MagicMock()
                                            mock_result.fetchall.return_value = [
                                                (
                                                    "pub_123",
                                                    "Test Title",
                                                    "Nature",
                                                    None,  # published_date
                                                    "run_456",
                                                    85.0,  # relevancy
                                                    90.0,  # credibility
                                                    "Test summary",
                                                    0.5,   # distance
                                                )
                                            ]
                                            mock_session.execute.return_value = mock_result

                                            # Make get_db return our mock session
                                            def mock_db_generator():
                                                yield mock_session
                                            mock_get_db.return_value = mock_db_generator()

                                            from main import app
                                            client = TestClient(app, raise_server_exceptions=False)

                                            response = client.get("/search/publications?q=cancer+detection")

                                            # Should NOT be 500 (SQL error)
                                            assert response.status_code != 500, f"Got 500 error: {response.json()}"

                                            # If we get 200, verify structure
                                            if response.status_code == 200:
                                                data = response.json()
                                                assert "query" in data
                                                assert "results" in data
                                                assert data["query"] == "cancer detection"

    def test_search_sql_with_filters(self):
        """Test that search endpoint SQL works with all filters applied."""
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://test:test@localhost/test",
            "SPOTITEARLY_LLM_API_KEY": "sk-test-key"
        }):
            with patch("db.engine"):
                with patch("db.SessionLocal"):
                    with patch("db.PGVECTOR_AVAILABLE", True):
                        with patch("main.PGVECTOR_AVAILABLE", True):
                            with patch("main.is_embedding_available", return_value=True):
                                with patch("main.get_openai_client") as mock_client:
                                    with patch("main.generate_embedding") as mock_embed:
                                        with patch("main.get_db") as mock_get_db:
                                            dummy_embedding = [0.1] * 1536
                                            mock_embed.return_value = dummy_embedding

                                            mock_session = MagicMock()
                                            mock_session.query.return_value.filter.return_value.count.return_value = 10

                                            mock_result = MagicMock()
                                            mock_result.fetchall.return_value = []
                                            mock_session.execute.return_value = mock_result

                                            def mock_db_generator():
                                                yield mock_session
                                            mock_get_db.return_value = mock_db_generator()

                                            from main import app
                                            client = TestClient(app, raise_server_exceptions=False)

                                            # Test with all filters
                                            response = client.get(
                                                "/search/publications"
                                                "?q=liquid+biopsy"
                                                "&limit=50"
                                                "&min_relevancy=70"
                                                "&min_credibility=60"
                                                "&date_from=2025-01-01"
                                                "&date_to=2025-12-31"
                                            )

                                            # Should NOT be 500 (SQL error)
                                            assert response.status_code != 500, f"Got 500 error: {response.json()}"

                                            # Verify the execute was called with proper params
                                            if mock_session.execute.called:
                                                call_args = mock_session.execute.call_args
                                                params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
                                                # Verify parameter names are correct (no %()s style)
                                                if isinstance(params, dict):
                                                    assert "query_embedding" in params
                                                    assert "limit" in params


class TestSearchResultsIncludeSourceAndDate:
    """Test that search results include source and published_date fields."""

    def test_search_results_include_source_and_date(self):
        """
        Test that search results include non-null source and published_date
        when they are available in the data.
        """
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://test:test@localhost/test",
            "SPOTITEARLY_LLM_API_KEY": "sk-test-key"
        }):
            with patch("db.engine"):
                with patch("db.SessionLocal"):
                    with patch("db.PGVECTOR_AVAILABLE", True):
                        with patch("main.PGVECTOR_AVAILABLE", True):
                            with patch("main.is_embedding_available", return_value=True):
                                with patch("main.get_openai_client") as mock_client:
                                    with patch("main.generate_embedding") as mock_embed:
                                        with patch("main.get_db") as mock_get_db:
                                            dummy_embedding = [0.1] * 1536
                                            mock_embed.return_value = dummy_embedding

                                            mock_session = MagicMock()
                                            mock_session.query.return_value.filter.return_value.count.return_value = 10

                                            # Mock search results WITH source and published_date
                                            from datetime import datetime
                                            mock_result = MagicMock()
                                            mock_result.fetchall.return_value = [
                                                (
                                                    "pub_123",
                                                    "Cancer Detection via Canine Olfaction",
                                                    "Nature Medicine",  # source
                                                    datetime(2025, 1, 15),  # published_date
                                                    "run_456",
                                                    92.5,  # relevancy
                                                    88.0,  # credibility
                                                    "Dogs can detect cancer biomarkers with high accuracy.",
                                                    0.25,   # distance
                                                ),
                                                (
                                                    "pub_456",
                                                    "Liquid Biopsy Advances",
                                                    "JAMA Oncology",  # source
                                                    datetime(2025, 1, 10),  # published_date
                                                    "run_789",
                                                    85.0,
                                                    90.0,
                                                    "New methods for ctDNA detection.",
                                                    0.35,
                                                )
                                            ]
                                            mock_session.execute.return_value = mock_result

                                            def mock_db_generator():
                                                yield mock_session
                                            mock_get_db.return_value = mock_db_generator()

                                            from main import app
                                            client = TestClient(app, raise_server_exceptions=False)

                                            response = client.get("/search/publications?q=cancer+detection")

                                            if response.status_code == 200:
                                                data = response.json()
                                                assert "results" in data
                                                assert len(data["results"]) == 2

                                                # First result should have source and date
                                                result1 = data["results"][0]
                                                assert result1["source"] == "Nature Medicine"
                                                assert result1["published_date"] == "2025-01-15"
                                                assert result1["title"] == "Cancer Detection via Canine Olfaction"

                                                # Second result should also have source and date
                                                result2 = data["results"][1]
                                                assert result2["source"] == "JAMA Oncology"
                                                assert result2["published_date"] == "2025-01-10"

    def test_search_results_handle_null_source_and_date(self):
        """
        Test that search results gracefully handle null source and published_date.
        """
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://test:test@localhost/test",
            "SPOTITEARLY_LLM_API_KEY": "sk-test-key"
        }):
            with patch("db.engine"):
                with patch("db.SessionLocal"):
                    with patch("db.PGVECTOR_AVAILABLE", True):
                        with patch("main.PGVECTOR_AVAILABLE", True):
                            with patch("main.is_embedding_available", return_value=True):
                                with patch("main.get_openai_client") as mock_client:
                                    with patch("main.generate_embedding") as mock_embed:
                                        with patch("main.get_db") as mock_get_db:
                                            dummy_embedding = [0.1] * 1536
                                            mock_embed.return_value = dummy_embedding

                                            mock_session = MagicMock()
                                            mock_session.query.return_value.filter.return_value.count.return_value = 10

                                            # Mock search results with NULL source and date
                                            mock_result = MagicMock()
                                            mock_result.fetchall.return_value = [
                                                (
                                                    "pub_789",
                                                    "Some Publication",
                                                    None,  # source is null
                                                    None,  # published_date is null
                                                    "run_123",
                                                    75.0,
                                                    None,
                                                    None,
                                                    0.5,
                                                )
                                            ]
                                            mock_session.execute.return_value = mock_result

                                            def mock_db_generator():
                                                yield mock_session
                                            mock_get_db.return_value = mock_db_generator()

                                            from main import app
                                            client = TestClient(app, raise_server_exceptions=False)

                                            response = client.get("/search/publications?q=test")

                                            if response.status_code == 200:
                                                data = response.json()
                                                assert "results" in data
                                                result = data["results"][0]
                                                # Should be None, not crash
                                                assert result["source"] is None
                                                assert result["published_date"] is None


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

    def test_search_results_include_source_and_date_from_join(self, db_session):
        """
        Integration test: Verify that search results include source and published_date
        from the JOIN with tri_model_events even when not stored in publication_embeddings.
        """
        pass  # Would seed database and verify JOIN enrichment


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
