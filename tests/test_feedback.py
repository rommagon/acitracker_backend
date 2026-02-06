"""
Tests for weekly digest feedback endpoint.
"""

import os
import hmac
import time
import hashlib
from urllib.parse import urlencode
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure required env vars exist before importing main/db modules.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("DIGEST_FEEDBACK_SECRET", "test-secret")

import main  # noqa: E402


FIXTURE_SIGNED_PARAMS = {
    "p": "pub-123",
    "w": "2026-01-05",
    "e": "2026-01-11",
    "v": "up",
    "t": "1736035200",
}
FIXTURE_CANONICAL_QUERY = "e=2026-01-11&p=pub-123&t=1736035200&v=up&w=2026-01-05"
FIXTURE_SIGNATURE = "1c7759a2abaee58aec9daba5233341b87dbc2f33f220d188131cfca11f5312ec"


def build_signature(secret: str, params: dict) -> str:
    canonical_query = urlencode([(key, str(params[key])) for key in sorted(["p", "w", "e", "v", "t"])])
    return hmac.new(secret.encode("utf-8"), canonical_query.encode("utf-8"), hashlib.sha256).hexdigest()


@pytest.fixture
def feedback_client():
    mock_db = MagicMock()

    def override_get_db():
        yield mock_db

    main.app.dependency_overrides[main.get_db] = override_get_db
    with patch.object(main, "init_db", return_value=None):
        client = TestClient(main.app, raise_server_exceptions=False)
        yield client, mock_db
    main.app.dependency_overrides.clear()


def test_deterministic_signature_fixture():
    assert main.build_feedback_canonical_query(FIXTURE_SIGNED_PARAMS) == FIXTURE_CANONICAL_QUERY
    assert build_signature("test-secret", FIXTURE_SIGNED_PARAMS) == FIXTURE_SIGNATURE


def test_valid_signature_accepted(feedback_client):
    client, _ = feedback_client

    now = int(time.time())
    params = {
        "p": "pub-abc",
        "w": "2026-01-05",
        "e": "2026-01-11",
        "v": "up",
        "t": str(now),
    }
    params["s"] = build_signature("test-secret", params)

    response = client.get("/feedback", params=params, headers={"User-Agent": "pytest"})
    assert response.status_code == 200
    assert "Thanks, your feedback was recorded." in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_invalid_signature_rejected(feedback_client):
    client, _ = feedback_client

    now = int(time.time())
    response = client.get(
        "/feedback",
        params={
            "p": "pub-abc",
            "w": "2026-01-05",
            "e": "2026-01-11",
            "v": "up",
            "t": str(now),
            "s": "0" * 64,
        },
    )
    assert response.status_code == 403
    assert "Invalid feedback signature." in response.text


def test_expired_link_rejected(feedback_client):
    client, _ = feedback_client

    old_timestamp = str(int(time.time()) - 1000)
    params = {
        "p": "pub-abc",
        "w": "2026-01-05",
        "e": "2026-01-11",
        "v": "up",
        "t": old_timestamp,
    }
    params["s"] = build_signature("test-secret", params)

    with patch.dict(os.environ, {"FEEDBACK_MAX_AGE_SECONDS": "10"}, clear=False):
        response = client.get("/feedback", params=params)

    assert response.status_code == 410
    assert "This feedback link has expired." in response.text


def test_invalid_vote_rejected(feedback_client):
    client, _ = feedback_client

    now = int(time.time())
    params = {
        "p": "pub-abc",
        "w": "2026-01-05",
        "e": "2026-01-11",
        "v": "maybe",
        "t": str(now),
    }
    params["s"] = build_signature("test-secret", params)

    response = client.get("/feedback", params=params)
    assert response.status_code == 400
    assert "Invalid vote value. Use 'up' or 'down'." in response.text


def test_successful_db_insert(feedback_client):
    client, mock_db = feedback_client

    now = int(time.time())
    params = {
        "p": "pub-db-check",
        "w": "2026-01-05",
        "e": "2026-01-11",
        "v": "down",
        "t": str(now),
    }
    params["s"] = build_signature("test-secret", params)

    response = client.get(
        "/feedback",
        params=params,
        headers={
            "X-Forwarded-For": "203.0.113.9",
            "User-Agent": "feedback-test-agent/1.0",
        },
    )

    assert response.status_code == 200
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()

    feedback_row = mock_db.add.call_args[0][0]
    assert feedback_row.publication_id == "pub-db-check"
    assert feedback_row.vote == "down"
    assert str(feedback_row.week_start) == "2026-01-05"
    assert str(feedback_row.week_end) == "2026-01-11"
    assert feedback_row.source_ip == "203.0.113.9"
    assert feedback_row.user_agent == "feedback-test-agent/1.0"
    assert feedback_row.context_json == f'{{"t": {now}}}'
