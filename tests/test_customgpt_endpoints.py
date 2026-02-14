"""
Tests for CustomGPT-facing API endpoints:
- GET /daily-must-reads
- GET /weekly-must-reads
- GET /stats
- GET /whats-new

All CustomGPT endpoints now read from the centralized publications table.
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
from db import Run, Publication, PublicationEmbedding  # noqa: E402


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


def make_pub(pub_id="pub-001", title="Test Paper", score=85, agreement="high",
             claude_score=88, gemini_score=82, credibility_score=75,
             url="https://pubmed.ncbi.nlm.nih.gov/12345/",
             scoring_run_id="run-2026-02-10",
             published_date="2026-02-09"):
    """Create a mock Publication with centralized scoring columns."""
    pub = MagicMock(spec=Publication)
    pub.publication_id = pub_id
    pub.title = title
    pub.authors = "Smith J, Doe A"
    pub.source = "Nature Medicine"
    pub.venue = "Nature Medicine"
    pub.published_date = published_date
    pub.url = url
    pub.canonical_url = None
    pub.doi = None
    pub.pmid = "12345"
    pub.source_type = "pubmed"
    pub.raw_text = None
    pub.summary = "Base summary."
    pub.final_relevancy_score = score
    pub.final_relevancy_reason = "Relevant to ACI detection."
    pub.final_summary = "Tri-model synthesized summary."
    pub.claude_score = claude_score
    pub.gemini_score = gemini_score
    pub.agreement_level = agreement
    pub.confidence = "high"
    pub.evaluator_rationale = "Highly relevant to ACI detection."
    pub.disagreements = None
    pub.final_signals_json = None
    pub.credibility_score = credibility_score
    pub.credibility_reason = "Peer-reviewed journal article with strong methodology."
    pub.credibility_confidence = "high"
    pub.credibility_signals_json = None
    pub.scoring_run_id = scoring_run_id
    pub.scoring_updated_at = None
    pub.latest_run_id = scoring_run_id
    pub.created_at = datetime(2026, 2, 10, 6, 0, 0)
    pub.updated_at = datetime(2026, 2, 10, 6, 0, 0)
    pub.latest_relevancy_score = score
    pub.latest_credibility_score = credibility_score
    return pub


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
        pub_high = make_pub(pub_id="pub-001", score=85, claude_score=88, gemini_score=82)
        pub_low = make_pub(pub_id="pub-002", title="Low Score Paper", score=40,
                           claude_score=45, gemini_score=35)

        def side_effect_query(model):
            m = MagicMock()
            if model is Run:
                m.filter.return_value.order_by.return_value.first.return_value = run
                return m
            elif model is Publication:
                m.filter.return_value.order_by.return_value.all.return_value = [pub_high, pub_low]
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
        pub1 = make_pub(pub_id="pub-001", score=85)
        pub2 = make_pub(pub_id="pub-002", score=75)

        def side_effect_query(model):
            m = MagicMock()
            if model is Run:
                m.filter.return_value.order_by.return_value.first.return_value = run
            elif model is Publication:
                m.filter.return_value.order_by.return_value.all.return_value = [pub1, pub2]
            return m

        mock_db.query.side_effect = side_effect_query

        response = test_client.get("/daily-must-reads?threshold=60", headers=API_KEY_HEADER)
        assert response.status_code == 200
        data = response.json()
        assert data["note"] is not None
        assert "fewer than 5" in data["note"]
        assert len(data["papers"]) == 2

    def test_credibility_data_flows_through(self, client):
        """Credibility data now comes from publications table directly."""
        test_client, mock_db = client

        run = make_run()
        pub1 = make_pub(pub_id="pub-001", score=85, credibility_score=75)

        def side_effect_query(model):
            m = MagicMock()
            if model is Run:
                m.filter.return_value.order_by.return_value.first.return_value = run
            elif model is Publication:
                m.filter.return_value.order_by.return_value.all.return_value = [pub1]
            return m

        mock_db.query.side_effect = side_effect_query

        response = test_client.get("/daily-must-reads?threshold=60", headers=API_KEY_HEADER)
        assert response.status_code == 200
        data = response.json()
        assert data["papers"][0]["credibility_score"] == 75
        assert data["papers"][0]["credibility_reason"] == "Peer-reviewed journal article with strong methodology."


# ─── /weekly-must-reads ──────────────────────────────────────────────


class TestWeeklyMustReads:
    def test_requires_api_key(self, client):
        test_client, _ = client
        response = test_client.get("/weekly-must-reads")
        assert response.status_code == 401

    def test_returns_top_papers(self, client):
        test_client, mock_db = client

        pub1 = make_pub(pub_id="pub-001", score=95, credibility_score=80)
        pub2 = make_pub(pub_id="pub-002", score=88, title="Second Paper")

        chain_mock = MagicMock()
        chain_mock.filter.return_value = chain_mock
        chain_mock.order_by.return_value = chain_mock
        chain_mock.limit.return_value = chain_mock
        chain_mock.all.return_value = [pub1, pub2]
        chain_mock.scalar.return_value = 42

        def side_effect_query(*args):
            return chain_mock

        mock_db.query.side_effect = side_effect_query

        response = test_client.get("/weekly-must-reads?top_n=5&days=7", headers=API_KEY_HEADER)
        assert response.status_code == 200
        data = response.json()
        assert "papers" in data
        assert "period" in data
        assert data["period"]["days"] == 7
        assert data["papers"][0]["credibility_score"] == 80

    def test_empty_period(self, client):
        test_client, mock_db = client

        chain_mock = MagicMock()
        chain_mock.filter.return_value = chain_mock
        chain_mock.order_by.return_value = chain_mock
        chain_mock.limit.return_value = chain_mock
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
        pubs = [
            make_pub(pub_id="pub-001", score=95, agreement="high"),
            make_pub(pub_id="pub-002", score=80, title="Paper 2", agreement="high"),
            make_pub(pub_id="pub-003", score=45, title="Paper 3", agreement="low",
                     claude_score=70, gemini_score=30),
            make_pub(pub_id="pub-004", score=30, title="Paper 4", agreement="moderate"),
        ]

        def side_effect_query(model):
            m = MagicMock()
            if model is Run:
                m.filter.return_value.order_by.return_value.first.return_value = run
                return m
            elif model is Publication:
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
        assert data["top_papers"][0]["final_relevancy_score"] == 95

        assert len(data["high_agreement_highlights"]) <= 3

        assert len(data["notable_disagreements"]) <= 3
        if data["notable_disagreements"]:
            assert "max_score_delta" in data["notable_disagreements"][0]
