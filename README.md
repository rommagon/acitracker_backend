# AciTrack Backend API

A production-ready FastAPI service providing Postgres-backed access to academic publication tracking data for consumption by Custom GPT Actions and internal tools.

## Overview

AciTrack Backend provides HTTPS endpoints to access academic publication reports, tri-model evaluations, and must-read decisions stored in Render Postgres. The API supports both read and ingest operations with API key authentication.

## Features

- ✅ **Postgres-Backed** - Fast, reliable database storage on Render
- ✅ **REST API** - Clean endpoints for runs, papers, must-reads, and disagreements
- ✅ **API Key Auth** - Secure X-API-Key header authentication for all endpoints
- ✅ **Custom GPT Ready** - OpenAPI 3.0.1 schema with mode/run_id query params
- ✅ **Production Deploy** - Ready for Render deployment
- ✅ **Health Monitoring** - Database connectivity checks
- ✅ **CORS Configured** - Secure access from OpenAI domains
- ✅ **Bulk Ingest** - Idempotent upsert endpoints for pipeline data
- ✅ **SSL Support** - Automatic SSL configuration for Render Postgres

## Quick Start

### Prerequisites

- Python 3.11+
- Render Postgres database (or any Postgres instance)
- API key for authentication

### Local Development

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd acitracker_backend
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set environment variables**
   ```bash
   export DATABASE_URL="postgresql://user:password@host:port/database"
   export ACITRACK_API_KEY="your-secret-api-key"
   export DIGEST_FEEDBACK_SECRET="your-digest-feedback-secret"
   export FEEDBACK_MAX_AGE_SECONDS="7776000"  # optional
   ```

5. **Test database connection** (optional)
   ```bash
   python test_db_connection.py
   ```

6. **Run the server**
   ```bash
   python main.py
   ```

   Tables are automatically created on startup via SQLAlchemy.

7. **Test locally**
   ```bash
   curl http://localhost:8000/health
   curl -H "X-API-Key: your-secret-api-key" http://localhost:8000/runs/latest
   ```

## API Endpoints

### Public Endpoints (No Auth Required)

#### `GET /`
Returns API information and available endpoints.

**Response**: `application/json`

#### `GET /health`
Health check endpoint for monitoring service and database status.

**Response**: `application/json`
```json
{
  "status": "healthy",
  "db_connected": true,
  "db_host": "postgres.render.internal:5432",
  "time": "2025-01-24T10:30:00.000Z"
}
```

#### `GET /feedback`
Public endpoint for weekly digest thumbs up/down links. Returns a simple HTML page.

**Query Parameters (all required):**
- `p`: publication_id
- `w`: week_start (`YYYY-MM-DD`)
- `e`: week_end (`YYYY-MM-DD`)
- `v`: vote (`up` or `down`)
- `t`: unix timestamp seconds
- `s`: HMAC-SHA256 hex signature

**Signed URL format:**
```text
/feedback?p=<publication_id>&w=<week_start>&e=<week_end>&v=<up|down>&t=<unix_ts>&s=<hex_signature>
```

**Canonical signature input:**
- Use only keys `p,w,e,v,t`
- Stringify values
- Sort by key
- URL-encode as query string

**Example:**
```text
/feedback?p=pub-123&w=2026-01-05&e=2026-01-11&v=up&t=1736035200&s=1c7759a2abaee58aec9daba5233341b87dbc2f33f220d188131cfca11f5312ec
```

### Read Endpoints (Require API Key)

All read endpoints require the `X-API-Key` header.

#### `GET /runs/latest`
Get the latest run for a specific mode.

**Query Parameters:**
- `mode` (optional): `tri-model-daily`, `daily`, or `weekly` (default: `tri-model-daily`)

**Response**: `application/json`
```json
{
  "run_id": "tri-model-daily-20250124-103045",
  "mode": "tri-model-daily",
  "started_at": "2025-01-24T10:30:45.000Z",
  "window_start": "2025-01-23T00:00:00.000Z",
  "window_end": "2025-01-24T00:00:00.000Z",
  "counts": { "total": 150, "high_agreement": 120 },
  "config": { "threshold": 0.8 },
  "artifacts": { "must_reads_count": 5 },
  "created_at": "2025-01-24T10:30:45.000Z",
  "updated_at": "2025-01-24T10:30:45.000Z"
}
```

**Example:**
```bash
curl -H "X-API-Key: your-api-key" \
  "https://your-app.onrender.com/runs/latest?mode=tri-model-daily"
```

#### `GET /runs/{run_id}`
Get a specific run by run_id.

**Path Parameters:**
- `run_id`: Unique run identifier

**Response**: Same as `/runs/latest`

**Example:**
```bash
curl -H "X-API-Key: your-api-key" \
  "https://your-app.onrender.com/runs/tri-model-daily-20250124-103045"
```

#### `GET /must-reads`
Get must-read publications.

**Query Parameters** (one required):
- `mode` (optional): Get must-reads for latest run of this mode
- `run_id` (optional): Get must-reads for specific run_id

**Response**: `application/json`
```json
{
  "run_id": "tri-model-daily-20250124-103045",
  "mode": "tri-model-daily",
  "must_reads": [
    {
      "publication_id": "arxiv:2501.12345",
      "title": "Novel Approach to X",
      "relevancy_score": 95,
      "rationale": "..."
    }
  ],
  "created_at": "2025-01-24T10:30:45.000Z",
  "updated_at": "2025-01-24T10:30:45.000Z"
}
```

**Examples:**
```bash
# By mode (returns latest)
curl -H "X-API-Key: your-api-key" \
  "https://your-app.onrender.com/must-reads?mode=tri-model-daily"

# By run_id (specific run)
curl -H "X-API-Key: your-api-key" \
  "https://your-app.onrender.com/must-reads?run_id=tri-model-daily-20250124-103045"
```

#### `GET /paper/{publication_id}`
Get tri-model evaluation data for a specific publication.

**Path Parameters:**
- `publication_id`: Publication identifier (e.g., `arxiv:2501.12345`)

**Query Parameters:**
- `run_id` (optional): Filter by specific run_id (default: latest)

**Response**: `application/json`
```json
{
  "id": 123,
  "run_id": "tri-model-daily-20250124-103045",
  "mode": "tri-model-daily",
  "publication_id": "arxiv:2501.12345",
  "title": "Novel Approach to X",
  "agreement_level": "high",
  "disagreements": "Minor differences in score magnitude",
  "evaluator_rationale": "All models agree this is highly relevant",
  "claude_review": { "relevancy_score": 95, "reasoning": "..." },
  "gemini_review": { "relevancy_score": 92, "reasoning": "..." },
  "gpt_eval": { "relevancy_score": 94, "reasoning": "..." },
  "final_relevancy_score": 94.0,
  "created_at": "2025-01-24T10:30:45.000Z"
}
```

**Example:**
```bash
curl -H "X-API-Key: your-api-key" \
  "https://your-app.onrender.com/paper/arxiv:2501.12345"
```

#### `GET /disagreements`
Get tri-model events with disagreements, with optional filtering.

**Query Parameters:**
- `run_id` (optional): Filter by specific run_id
- `agreement` (optional): Comma-separated agreement levels (e.g., `low,moderate`)
- `min_delta` (optional): Minimum score delta between Claude and Gemini
- `limit` (optional): Maximum results to return (default: 50, max: 500)

**Response**: `application/json`
```json
{
  "count": 15,
  "filters": {
    "run_id": null,
    "agreement": "low,moderate",
    "min_delta": 20,
    "limit": 50
  },
  "disagreements": [
    {
      "id": 456,
      "run_id": "tri-model-daily-20250124-103045",
      "publication_id": "arxiv:2501.67890",
      "title": "Controversial Paper",
      "agreement_level": "low",
      "disagreements": "Claude rated high, Gemini rated low",
      "claude_score": 85,
      "gemini_score": 40,
      "score_delta": 45,
      "final_relevancy_score": 62.5,
      "created_at": "2025-01-24T10:30:45.000Z"
    }
  ]
}
```

**Examples:**
```bash
# All low/moderate agreement cases
curl -H "X-API-Key: your-api-key" \
  "https://your-app.onrender.com/disagreements?agreement=low,moderate"

# Large score deltas only
curl -H "X-API-Key: your-api-key" \
  "https://your-app.onrender.com/disagreements?min_delta=30"

# Specific run with filters
curl -H "X-API-Key: your-api-key" \
  "https://your-app.onrender.com/disagreements?run_id=tri-model-daily-20250124-103045&agreement=low&limit=20"
```

### Ingest Endpoints (Require API Key)

All ingest endpoints require the `X-API-Key` header and accept JSON payloads.

#### `POST /ingest/run`
Upsert a run record (idempotent by run_id).

**Request Body:**
```json
{
  "run_id": "tri-model-daily-20250124-103045",
  "mode": "tri-model-daily",
  "started_at": "2025-01-24T10:30:45Z",
  "window_start": "2025-01-23T00:00:00Z",
  "window_end": "2025-01-24T00:00:00Z",
  "counts": { "total": 150, "high_agreement": 120 },
  "config": { "threshold": 0.8 },
  "artifacts": { "must_reads_count": 5 }
}
```

**Response:** `application/json` (200 OK)
```json
{
  "status": "success",
  "run_id": "tri-model-daily-20250124-103045",
  "mode": "tri-model-daily",
  "created_at": "2025-01-24T10:30:45.000Z",
  "updated_at": "2025-01-24T10:30:45.000Z"
}
```

**Example:**
```bash
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"run_id":"tri-model-daily-20250124-103045","mode":"tri-model-daily","started_at":"2025-01-24T10:30:45Z"}' \
  "https://your-app.onrender.com/ingest/run"
```

#### `POST /ingest/tri-model-events`
Bulk upsert tri-model events (idempotent by run_id + publication_id).

**Request Body:**
```json
{
  "run_id": "tri-model-daily-20250124-103045",
  "mode": "tri-model-daily",
  "events": [
    {
      "publication_id": "arxiv:2501.12345",
      "title": "Novel Approach to X",
      "agreement_level": "high",
      "disagreements": null,
      "evaluator_rationale": "All models agree",
      "claude_review": { "relevancy_score": 95 },
      "gemini_review": { "relevancy_score": 92 },
      "gpt_eval": { "relevancy_score": 94 },
      "final_relevancy_score": 94.0
    }
  ]
}
```

**Response:** `application/json`
```json
{
  "status": "success",
  "run_id": "tri-model-daily-20250124-103045",
  "mode": "tri-model-daily",
  "inserted": 150,
  "updated": 0,
  "total": 150
}
```

**Example:**
```bash
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d @events.json \
  "https://your-app.onrender.com/ingest/tri-model-events"
```

#### `POST /ingest/must-reads`
Upsert must-reads for a run (idempotent by run_id).

**Request Body:**
```json
{
  "run_id": "tri-model-daily-20250124-103045",
  "mode": "tri-model-daily",
  "must_reads": [
    {
      "publication_id": "arxiv:2501.12345",
      "title": "Novel Approach to X",
      "relevancy_score": 95
    }
  ]
}
```

**Response:** `application/json`
```json
{
  "status": "success",
  "run_id": "tri-model-daily-20250124-103045",
  "mode": "tri-model-daily",
  "created_at": "2025-01-24T10:30:45.000Z",
  "updated_at": "2025-01-24T10:30:45.000Z"
}
```

**Example:**
```bash
curl -X POST \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d @must_reads.json \
  "https://your-app.onrender.com/ingest/must-reads"
```

## Deployment

### Render Deployment (Step-by-Step)

#### 1. Create Render Postgres Database

1. Go to [Render Dashboard](https://dashboard.render.com/)
2. Click **New +** → **PostgreSQL**
3. Configure database:
   - Name: `acitrack-db` (or your choice)
   - Database: `acitrack` (auto-created)
   - User: `acitrack_user` (auto-created)
   - Region: Choose closest to you
   - Plan: **Free** (for testing) or **Starter** (for production)
4. Click **Create Database**
5. Copy the **Internal Database URL** (starts with `postgres://`)

#### 2. Create Render Web Service

1. In Render Dashboard, click **New +** → **Web Service**
2. Connect your GitHub repository
3. Configure service:
   - **Name**: `acitrack-backend` (or your choice)
   - **Region**: Same as database
   - **Branch**: `main`
   - **Runtime**: `Python 3`
   - **Build Command**:
     ```bash
     pip install -r requirements.txt
     ```
   - **Start Command**:
     ```bash
     uvicorn main:app --host 0.0.0.0 --port $PORT
     ```

#### 3. Set Environment Variables

In the web service settings, add these environment variables:

| Variable | Value | Notes |
|----------|-------|-------|
| `DATABASE_URL` | `<from Render Postgres>` | Use **Internal Database URL** from step 1 |
| `ACITRACK_API_KEY` | `<generate-strong-key>` | Use `openssl rand -hex 32` or similar |
| `LOG_LEVEL` | `INFO` | Optional: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `PYTHON_VERSION` | `3.11.0` | Optional: specify Python version |

**Important:** Use the **Internal Database URL** (not External) for better performance and security.

#### 4. Deploy

1. Click **Create Web Service**
2. Render will automatically:
   - Install dependencies
   - Initialize database tables (via startup event)
   - Start the FastAPI server
3. Monitor deployment in the **Logs** tab
4. Once deployed, your service URL will be: `https://your-app-name.onrender.com`

#### 5. Verify Deployment

Test your endpoints:

```bash
# Health check (no auth required)
curl https://your-app.onrender.com/health

# Should return: {"status":"healthy","db_connected":true,...}
```

### Quick Deployment Checklist

After deploying to Render, verify these steps:

- [ ] **Health check passes**: `/health` returns `"status": "healthy"` and `"db_connected": true`
- [ ] **Database tables created**: Check Render Postgres dashboard or logs for table creation
- [ ] **API key works**: Test authenticated endpoint with X-API-Key header
- [ ] **Ingest a test run**:
  ```bash
  curl -X POST https://your-app.onrender.com/ingest/run \
    -H "X-API-Key: your-api-key" \
    -H "Content-Type: application/json" \
    -d '{
      "run_id": "test-run-001",
      "mode": "tri-model-daily",
      "started_at": "2025-01-24T10:00:00Z"
    }'
  ```
- [ ] **Query the run**:
  ```bash
  curl https://your-app.onrender.com/runs/latest?mode=tri-model-daily \
    -H "X-API-Key: your-api-key"
  ```
- [ ] **Custom GPT configured**: Update openapi.json server URL and add to Custom GPT Actions

### Database Setup

The database schema is **automatically created on application startup** via SQLAlchemy's `Base.metadata.create_all()`.

Tables created:

- **runs**: Run metadata (run_id, mode, timestamps, JSON configs)
- **tri_model_events**: Tri-model evaluations (run_id, publication_id, agreement, reviews)
- **must_reads**: Must-read decisions per run (run_id, must_reads_json)
- **weekly_digest_feedback**: Digest click feedback (publication_id, week window, vote, request metadata)

For production schema changes, use Alembic migrations (see Development section).

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | - | Postgres connection string (format: `postgresql://user:pass@host:port/db`). SSL mode automatically added for Render. |
| `ACITRACK_API_KEY` | Yes | - | Secret API key for X-API-Key header authentication |
| `DIGEST_FEEDBACK_SECRET` | Yes | - | Secret used to verify `GET /feedback` signed links |
| `FEEDBACK_MAX_AGE_SECONDS` | No | `7776000` | Max age for signed feedback links (default 90 days) |
| `PORT` | No | `8000` | Port to run server (auto-set by Render to `10000`) |
| `LOG_LEVEL` | No | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `PYTHON_VERSION` | No | `3.11.0` | Python version (Render-specific) |

### SSL Configuration

The backend **automatically adds `sslmode=require`** to `DATABASE_URL` if not present. This ensures secure connections to Render Postgres without manual configuration.

Safe for local development: if `sslmode` is already in your local DATABASE_URL, it won't be modified.

## Security

- **API key authentication** via `X-API-Key` header (required for protected read/ingest endpoints)
- **HTTPS enforced** by Render platform
- **SSL/TLS** for Postgres connections (automatic)
- **CORS restricted** to OpenAI domains (`chat.openai.com`, `chatgpt.com`)
- **Read-only Custom GPT access** (uses read endpoints only)
- **Ingest endpoints** require same API key (used by internal pipeline)
- **Digest feedback signatures** verified with `DIGEST_FEEDBACK_SECRET` using HMAC-SHA256

## Custom GPT Integration

1. **Deploy to Render** and get your production URL (e.g., `https://acitrack-backend.onrender.com`)
2. **Set API Key** in Render environment variables
3. **Update openapi.json** with your server URL (line 10):
   ```json
   "servers": [{"url": "https://your-app.onrender.com"}]
   ```
4. **Configure Custom GPT Actions:**
   - In ChatGPT → GPT Builder → Actions
   - Paste contents of `openapi.json`
   - Add Authentication: **API Key**
     - Auth Type: `API Key`
     - API Key: `your-api-key`
     - Auth Header: `X-API-Key`
   - Save

5. **Test Custom GPT:**
   - "Show me the latest tri-model daily run"
   - "What are today's must-read papers?"
   - "Show disagreements with large score deltas"

## Project Structure

```
acitracker_backend/
├── main.py                  # FastAPI application with all endpoints
├── db.py                    # SQLAlchemy models and database setup
├── requirements.txt         # Python dependencies
├── openapi.json            # OpenAPI schema for Custom GPT
├── test_db_connection.py   # Database smoke test script
├── README.md               # This file
└── .gitignore              # Git ignore rules
```

## Monitoring

### Health Check

Monitor service health:
```bash
curl https://your-app.onrender.com/health
```

Expected response:
```json
{
  "status": "healthy",
  "db_connected": true,
  "db_host": "dpg-xxxxx-a.oregon-postgres.render.com:5432",
  "time": "2025-01-24T10:30:00.123456"
}
```

### Uptime Monitoring

Set up free monitoring with [UptimeRobot](https://uptimerobot.com) or [Better Uptime](https://betteruptime.com):

1. Create new HTTP(s) monitor
2. URL: `https://your-app.onrender.com/health`
3. Check interval: 5 minutes
4. Alert contacts: Your email/Slack

### Render Monitoring

Use Render's built-in monitoring:

- **Logs**: View real-time application logs in the dashboard
- **Metrics**: CPU, memory, and request metrics
- **Database**: Postgres connection count, query performance, storage

### Application Logs

View logs in Render Dashboard → Your Service → **Logs** tab

Look for:
- `Initializing database tables...` (on startup)
- `Database tables initialized successfully` (confirms tables created)
- Request logs with status codes
- Any error messages

## Troubleshooting

### Common Issues

**"DATABASE_URL environment variable is required"**
- Set `DATABASE_URL` in Render environment variables
- Use the **Internal Database URL** from your Render Postgres service
- Format: `postgresql://user:password@host:port/database`

**"Missing X-API-Key header"**
- Include API key in requests: `-H "X-API-Key: your-key"`
- For Custom GPT: Configure authentication in Actions settings (see Custom GPT Integration)

**"No runs found for mode=..."**
- Database is empty - use ingest endpoints to populate data
- Check mode parameter matches ingested data (`tri-model-daily`, `daily`, `weekly`)

**"Invalid JSON in must_reads_json"**
- Verify JSON structure in ingest payload
- Check database column has valid JSON text
- Use `json.dumps()` when serializing Python dicts

**Database connection errors**
- Verify DATABASE_URL is correct (check Render Postgres settings)
- Ensure web service and database are in the same region
- Check Render Postgres service is running (green status)
- Review Render logs for detailed error messages

**Tables not created**
- Check startup logs for "Database tables initialized successfully"
- If missing, restart the web service to trigger startup event
- Verify DATABASE_URL has correct permissions (user must have CREATE TABLE)

**SSL connection errors**
- The backend automatically adds `sslmode=require`
- If you see SSL errors, ensure DATABASE_URL doesn't have conflicting SSL params
- For local dev, use `sslmode=disable` in your local DATABASE_URL

### Checking Logs

**Render deployment logs:**
1. Go to Render Dashboard
2. Select your web service
3. Click **Logs** tab
4. Filter by level (Info, Warning, Error)

**Application startup logs:**
Look for these messages:
```
INFO - Initializing database tables...
INFO - Database tables initialized successfully
INFO - Uvicorn running on http://0.0.0.0:10000
```

**Database connection test:**
```bash
# Run locally with Render DATABASE_URL
export DATABASE_URL="<your-render-internal-url>"
python test_db_connection.py
```

## Performance

### Response Times
- Health check: ~50-100ms
- Read queries (indexed): ~100-300ms
- Bulk ingest (100 events): ~500ms-1s

### Optimization Tips
- Use `run_id` filters for faster queries (indexed)
- Limit disagreements query results (default 50, max 500)
- Use Render's **Internal Database URL** for faster connections
- Add custom indexes via Alembic migrations for specific query patterns
- Upgrade to Render Standard plan for better performance

## Cost Estimates

### Render Pricing (as of 2025)

**Free Tier:**
- Web Service: 750 hours/month free
- Postgres: Free with 1GB storage, limited connections
- Good for: Testing, low-traffic demos

**Starter Plan (~$7-15/month):**
- Web Service: $7/month (512MB RAM)
- Postgres Starter: $7/month (1GB RAM, 10GB storage)
- Good for: Production use, Custom GPT

**Professional Plan (~$25+/month):**
- Web Service: $25/month (4GB RAM, auto-scaling)
- Postgres Standard: $20/month (4GB RAM, 100GB storage, backups)
- Good for: High traffic, multiple users

### Database Storage
- Estimated usage: ~100MB for 1 year of daily runs
- Render Postgres Free: 1GB storage
- Render Postgres Starter: 10GB storage

## Migration from Google Drive

This version replaces the Google Drive backend. Key changes:

- **No Drive dependencies**: Removed `google-api-python-client`, `google-auth` packages
- **Database-first**: All data stored in Postgres (runs, events, must_reads)
- **Ingest endpoints**: Pipeline now POSTs data to `/ingest/*` endpoints
- **API key auth**: Required for all endpoints (except / and /health)
- **No caching needed**: Postgres is fast enough for real-time queries
- **SSL automatic**: Render Postgres SSL configured automatically

## Development

### Running Tests

```bash
# Install dev dependencies
pip install pytest pytest-cov

# Run tests
pytest tests/

# With coverage
pytest --cov=. tests/
```

### Database Migrations (Production)

For schema changes, use Alembic:

```bash
# Install Alembic
pip install alembic

# Initialize (one-time)
alembic init migrations

# Edit alembic.ini to point to your DATABASE_URL
# Edit migrations/env.py to import your models

# Create migration
alembic revision --autogenerate -m "Add new column"

# Apply migration locally
alembic upgrade head

# Deploy to Render (migrations run on startup via build command)
# Update Build Command: pip install -r requirements.txt && alembic upgrade head
```

### Local Development with Render Database

```bash
# Get Internal Database URL from Render Postgres dashboard
export DATABASE_URL="postgresql://user:pass@dpg-xxxxx.oregon-postgres.render.com/db"

# Run locally (connects to Render Postgres)
python main.py

# Test with local API key
export ACITRACK_API_KEY="dev-test-key-12345"
curl -H "X-API-Key: dev-test-key-12345" http://localhost:8000/runs/latest
```

## API Versioning

Current version: **2.0.0**

- Breaking changes: Major version bump (1.x → 2.x)
- New endpoints: Minor version bump (2.0 → 2.1)
- Bug fixes: Patch version bump (2.0.0 → 2.0.1)

## Support

For issues:
1. Check this README troubleshooting section
2. Review Render logs (Dashboard → Service → Logs)
3. Test `/health` endpoint for connectivity
4. Run `python test_db_connection.py` locally
5. Contact team lead or create GitHub issue

## License

Internal use only - SpotItEarly

## Credits

Built with:
- [FastAPI](https://fastapi.tiangolo.com/) - Modern Python web framework
- [SQLAlchemy](https://www.sqlalchemy.org/) - SQL toolkit and ORM
- [Render](https://render.com) - Deployment platform and Postgres hosting
- [psycopg2](https://www.psycopg.org/) - PostgreSQL adapter

---

**Status**: ✅ Production Ready

**Last Updated**: 2025-01-24

**Maintained By**: SpotItEarly Engineering Team
