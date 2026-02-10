"""
Tests for CustomGPT-facing API endpoints:
- GET /daily-must-reads
- GET /weekly-must-reads
- GET /stats
- GET /whats-new
"""

import os
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ACITRACK_API_KEY", "test-key")

import main  # noqa: E402
from db import Run, TriModelEvent, MustRead, Publication, PublicationEmbedding  # noqa: E402


API_KEY_HEADER = {"X-API-Key": "test-key"}


def make_run(run_id="run-2026-02-10", mode="tri-model-daily", started_at=None):
    run = MagicMock(spec=Run)
    run.run_id = run_id
    run.mode = mode
    run.started_at = started_at or datetime(2026, 2, 10, 6, 0, 0)
    run.window_start = datetime(2026, 2, 9, 0, 0, 0)
    run.window_end = datetime(2026, 2, 10, 0, 0, 0)
    run.counts_json = json.dumps({"total_found": 52, "scored": 45, "must_reads": 8})
    run.config_json = None
    run.artifacts_json = None
    run.created_at = datetime(2026, 2, 10, 6, 0, 0)
    run.updated_at = datetime(2026, 2, 10, 6, 0, 0)
    return run


def make_event(pub_id="pub-001", title="Test Paper", score=85.0, agreement="high",
               claude_score=88, gemini_score=82, gpt_score=85, run_id="run-2026-02-10"):
    event = MagicMock(spec=TriModelEvent)
    event.id = 1
    event.run_id = run_id
    event.mode = "tri-model-daily"
    event.publication_id = pub_id
    event.title = title
    event.agreement_level = agreement
    event.disagreements = None
    event.evaluator_rationale = "Highly relevant to ACI detection."
    event.claude_review_json = json.dumps({"relevancy_score": claude_score, "summary": "Claude summary."})
    event.gemini_review_json = json.dumps({"relevancy_score": gemini_score, "summary": "Gemini summary."})
    event.gpt_eval_json = json.dumps({"relevancy_score": gpt_score, "summary": "GPT summary."})
    event.final_relevancy_score = score
    event.created_at = datetime(2026, 2, 10, 6, 0, 0)
    return event


def make_publication(pub_id="pub-001", title="Test Paper", url="https://pubmed.ncbi.nlm.nih.gov/12345/"):
    pub = MagicMock(spec=Publication)
    pub.publication_id = pub_id
    pub.title = title
    pub.source = "Nature Medicine"
    pub.published_date = datetime(2026, 2, 9)
    pub.url = url
    pub.latest_run_id = "run-2026-02-10"
    pub.latest_relevancy_score = 85.0
    pub.latest_credibility_score = None
    return pub


def make_must_read(run_id="run-2026-02-10", pub_ids=None):
    if pub_ids is None:
        pub_ids = ["pub-001", "pub-002", "pub-003"]
    mr = MagicMock(spec=MustRead)
    mr.run_id = run_id
    mr.mode = "tri-model-daily"
    mr.must_reads_json = json.dumps([{"publication_id": pid} for pid in pub_ids])
    mr.created_at = datetime(2026, 2, 10, 6, 0, 0)
    mr.updated_at = datetime(2026, 2, 10, 6, 0, 0)
    return mr


@pytest.fixture
def client():
    mock_db = MagicMock()

    def override_get_db():
        yield mock_db

    main.app.dependency_overrides[main.get_db] = override_get_db
    with patch.object(main, "init_db", return_value=None):
        test_client = TestClient(main.app, raise_server_exceptions=False)
        yield test_client, mock_db
    main.app.dependency_overrides.clear()


# ─── /daily-must-reads ───────────────────────────────────────────────


class TestDailyMustReads:
    def test_requires_api_key(self, client):
        test_client, _ = client
        response = test_client.get("/daily-must-reads")
        assert response.status_code == 401

    def test_no_run_returns_404(self, client):
        test_client, mock_db = client
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        response = test_client.get("/daily-must-reads", headers=API_KEY_HEADER)
        assert response.status_code == 404

    def test_returns_papers_above_threshold(self, client):
        test_client, mock_db = client

        run = make_run()
        must_read = make_must_read(pub_ids=["pub-001", "pub-002"])
        event_high = make_event(pub_id="pub-001", score=85.0)
        event_low = make_event(pub_id="pub-002", score=40.0, title="Low Score Paper")
        pub1 = make_publication(pub_id="pub-001")
        pub2 = make_publication(pub_id="pub-002", title="Low Score Paper", url="https://example.com/2")

        # Mock the chained queries
        query_mock = MagicMock()
        filter_mock = MagicMock()

        def side_effect_query(model):
            m = MagicMock()
            if model == Run:
                m.filter.return_value.order_by.return_value.first.return_value = run
                return m
            elif model == MustRead:
                m.filter.return_value.first.return_value = must_read
                return m
            elif model == TriModelEvent:
                m.filter.return_value.all.return_value = [event_high, event_low]
                return m
            elif model == Publication:
                m.filter.return_value.all.return_value = [pub1, pub2]
                return m
            return m

        mock_db.query.side_effect = side_effect_query

        response = test_client.get("/daily-must-reads?threshold=60", headers=API_KEY_HEADER)
        assert response.status_code == 200
        data = response.json()
        assert data["above_threshold"] == 1
        assert data["below_threshold_count"] == 1
        assert len(data["papers"]) == 1
        assert data["papers"][0]["publication_id"] == "pub-001"
        assert data["papers"][0]["url"] == "https://pubmed.ncbi.nlm.nih.gov/12345/"
        assert data["papers"][0]["subscores"]["claude_score"] == 88

    def test_note_when_fewer_than_5(self, client):
        test_client, mock_db = client

        run = make_run()
        must_read = make_must_read(pub_ids=["pub-001", "pub-002"])
        event1 = make_event(pub_id="pub-001", score=85.0)
        event2 = make_event(pub_id="pub-002", score=75.0)
        pub1 = make_publication(pub_id="pub-001")
        pub2 = make_publication(pub_id="pub-002")

        def side_effect_query(model):
            m = MagicMock()
            if model == Run:
                m.filter.return_value.order_by.return_value.first.return_value = run
            elif model == MustRead:
                m.filter.return_value.first.return_value = must_read
            elif model == TriModelEvent:
                m.filter.return_value.all.return_value = [event1, event2]
            elif model == Publication:
                m.filter.return_value.all.return_value = [pub1, pub2]
            return m

        mock_db.query.side_effect = side_effect_query

        response = test_client.get("/daily-must-reads?threshold=60", headers=API_KEY_HEADER)
        assert response.status_code == 200
        data = response.json()
        assert data["note"] is not None
        assert "fewer than 5" in data["note"]
        assert len(data["papers"]) == 2


# ─── /weekly-must-reads ──────────────────────────────────────────────


class TestWeeklyMustReads:
    def test_requires_api_key(self, client):
        test_client, _ = client
        response = test_client.get("/weekly-must-reads")
        assert response.status_code == 401

    def test_returns_top_papers(self, client):
        test_client, mock_db = client

        event1 = make_event(pub_id="pub-001", score=95.0)
        event2 = make_event(pub_id="pub-002", score=88.0, title="Second Paper")
        pub1 = make_publication(pub_id="pub-001")
        pub2 = make_publication(pub_id="pub-002", title="Second Paper")

        # Use a flexible chainable mock that returns sensible defaults
        chain_mock = MagicMock()
        # Make all chain methods return the same mock for flexibility
        chain_mock.filter.return_value = chain_mock
        chain_mock.group_by.return_value = chain_mock
        chain_mock.join.return_value = chain_mock
        chain_mock.order_by.return_value = chain_mock
        chain_mock.limit.return_value = chain_mock
        chain_mock.subquery.return_value = MagicMock()
        chain_mock.all.return_value = [event1, event2]
        chain_mock.scalar.return_value = 42.0

        pub_mock = MagicMock()
        pub_mock.filter.return_value.all.return_value = [pub1, pub2]

        def side_effect_query(*args):
            if len(args) == 1 and args[0] is Publication:
                return pub_mock
            return chain_mock

        mock_db.query.side_effect = side_effect_query

        response = test_client.get("/weekly-must-reads?top_n=5&days=7", headers=API_KEY_HEADER)
        assert response.status_code == 200
        data = response.json()
        assert "papers" in data
        assert "period" in data
        assert data["period"]["days"] == 7

    def test_empty_period(self, client):
        test_client, mock_db = client

        chain_mock = MagicMock()
        chain_mock.filter.return_value = chain_mock
        chain_mock.group_by.return_value = chain_mock
        chain_mock.join.return_value = chain_mock
        chain_mock.order_by.return_value = chain_mock
        chain_mock.limit.return_value = chain_mock
        chain_mock.subquery.return_value = MagicMock()
        chain_mock.all.return_value = []
        chain_mock.scalar.return_value = 0

        def side_effect_query(*args):
            return chain_mock

        mock_db.query.side_effect = side_effect_query

        response = test_client.get("/weekly-must-reads", headers=API_KEY_HEADER)
        assert response.status_code == 200
        data = response.json()
        assert data["papers"] == []
        assert data["total_scored_this_period"] == 0


# ─── /stats ──────────────────────────────────────────────────────────


class TestStats:
    def test_requires_api_key(self, client):
        test_client, _ = client
        response = test_client.get("/stats")
        assert response.status_code == 401

    def test_returns_all_sections(self, client):
        test_client, mock_db = client

        run = make_run()

        # Flexible chainable mock
        chain_mock = MagicMock()
        chain_mock.filter.return_value = chain_mock
        chain_mock.order_by.return_value = chain_mock
        chain_mock.group_by.return_value = chain_mock
        chain_mock.first.return_value = run
        chain_mock.scalar.return_value = 100
        chain_mock.all.return_value = [("high", 30), ("moderate", 15), ("low", 5)]

        def side_effect_query(*args):
            return chain_mock

        mock_db.query.side_effect = side_effect_query

        response = test_client.get("/stats", headers=API_KEY_HEADER)
        assert response.status_code == 200
        data = response.json()
        assert "publications" in data
        assert "latest_daily_run" in data
        assert "embeddings" in data
        assert "scoring" in data
        assert "system" in data
        assert "generated_at" in data


# ─── /whats-new ──────────────────────────────────────────────────────


class TestWhatsNew:
    def test_requires_api_key(self, client):
        test_client, _ = client
        response = test_client.get("/whats-new")
        assert response.status_code == 401

    def test_no_run_returns_404(self, client):
        test_client, mock_db = client
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        response = test_client.get("/whats-new", headers=API_KEY_HEADER)
        assert response.status_code == 404

    def test_returns_summary_with_top_papers(self, client):
        test_client, mock_db = client

        run = make_run()
        events = [
            make_event(pub_id="pub-001", score=95.0, agreement="high"),
            make_event(pub_id="pub-002", score=80.0, agreement="high", title="Paper 2"),
            make_event(pub_id="pub-003", score=45.0, agreement="low", title="Paper 3",
                       claude_score=70, gemini_score=30, gpt_score=35),
            make_event(pub_id="pub-004", score=30.0, agreement="moderate", title="Paper 4"),
        ]
        pubs = [
            make_publication(pub_id="pub-001"),
            make_publication(pub_id="pub-002", title="Paper 2", url="https://example.com/2"),
            make_publication(pub_id="pub-003", title="Paper 3", url="https://example.com/3"),
            make_publication(pub_id="pub-004", title="Paper 4", url="https://example.com/4"),
        ]

        def side_effect_query(model):
            m = MagicMock()
            if model == Run:
                m.filter.return_value.order_by.return_value.first.return_value = run
                return m
            elif model == TriModelEvent:
                m.filter.return_value.all.return_value = events
                return m
            elif model == Publication:
                m.filter.return_value.all.return_value = pubs
                return m
            return m

        mock_db.query.side_effect = side_effect_query

        response = test_client.get("/whats-new", headers=API_KEY_HEADER)
        assert response.status_code == 200
        data = response.json()

        assert data["run_id"] == "run-2026-02-10"
        assert data["summary"]["total_papers_scored"] == 4
        assert data["summary"]["papers_above_60"] == 2
        assert data["summary"]["high_agreement_count"] == 2
        assert data["summary"]["low_agreement_count"] == 1

        assert len(data["top_papers"]) <= 5
        assert data["top_papers"][0]["final_relevancy_score"] == 95.0

        assert len(data["high_agreement_highlights"]) <= 3

        assert len(data["notable_disagreements"]) <= 3
        if data["notable_disagreements"]:
            assert "max_score_delta" in data["notable_disagreements"][0]
