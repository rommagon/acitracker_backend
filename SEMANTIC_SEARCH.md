# Semantic Search for AciTracker

This document describes the semantic search functionality added to the AciTracker backend, enabling natural language queries across all historical publications.

## Overview

The semantic search feature uses:
- **pgvector** - Postgres extension for efficient vector similarity search
- **OpenAI text-embedding-3-small** - Generates 1536-dimensional embeddings
- **L2 distance** - Measures similarity between query and publication embeddings

## Setup

### 1. Environment Variables

Add the OpenAI API key to your environment:

```bash
export SPOTITEARLY_LLM_API_KEY="sk-..."
```

### 2. Database Extension

The pgvector extension is automatically enabled on startup. For Render Postgres, this should work automatically. If you need to enable it manually:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

New dependencies added:
- `pgvector==0.3.6` - SQLAlchemy integration for pgvector
- `openai==1.59.5` - OpenAI API client
- `httpx==0.28.1` - HTTP client for OpenAI
- `tenacity==9.0.0` - Retry logic for API calls

### 4. Backfill Embeddings

Generate embeddings for existing publications:

```bash
# Process all publications (recommended for initial setup)
python scripts/backfill_embeddings.py

# Process with limit (for testing)
python scripts/backfill_embeddings.py --limit 100

# Process publications since a specific date
python scripts/backfill_embeddings.py --since-date 2025-01-01

# Dry run (show what would be processed)
python scripts/backfill_embeddings.py --dry-run --verbose
```

Options:
- `--limit N` - Process at most N publications
- `--since-date DATE` - Only process publications from runs after DATE (YYYY-MM-DD)
- `--batch-size N` - Process N publications per batch (default: 50)
- `--dry-run` - Show what would be processed without changes
- `--verbose` - Enable verbose logging

## API Endpoints

### GET /search/publications

Semantic search across all publications.

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| q | string | Yes | Search query (1-500 chars) |
| limit | int | No | Max results (default: 20, max: 100) |
| min_relevancy | float | No | Filter by min relevancy score (0-100) |
| min_credibility | float | No | Filter by min credibility score (0-100) |
| date_from | string | No | Filter from date (YYYY-MM-DD) |
| date_to | string | No | Filter to date (YYYY-MM-DD) |

**Example Request:**
```bash
curl -X GET "https://your-app.onrender.com/search/publications?q=dogs%20sniffing%20cancer&limit=10" \
  -H "X-API-Key: your-api-key"
```

**Example Response:**
```json
{
  "query": "dogs sniffing cancer",
  "count": 5,
  "filters": {
    "min_relevancy": null,
    "min_credibility": null,
    "date_from": null,
    "date_to": null
  },
  "results": [
    {
      "publication_id": "pub_123",
      "title": "Canine Olfactory Detection of Cancer Biomarkers",
      "source": "Nature Medicine",
      "published_date": "2025-01-15",
      "best_run_id": "run_456",
      "final_relevancy_score": 92.5,
      "credibility_score": 88.0,
      "final_summary": "Study demonstrates trained dogs can detect cancer with 95% accuracy...",
      "similarity": 0.8234
    }
  ]
}
```

### GET /search/status

Check semantic search availability.

**Example Request:**
```bash
curl -X GET "https://your-app.onrender.com/search/status" \
  -H "X-API-Key: your-api-key"
```

**Example Response:**
```json
{
  "pgvector_available": true,
  "openai_configured": true,
  "embedding_model": "text-embedding-3-small",
  "publications_with_embeddings": 1234,
  "total_unique_publications": 1500,
  "coverage_percent": 82.3,
  "search_available": true
}
```

### POST /ingest/embeddings

Generate embeddings for publications from a specific run.

**Request Body:**
```json
{
  "run_id": "run_123",
  "limit": 100,
  "force_regenerate": false
}
```

**Example Request:**
```bash
curl -X POST "https://your-app.onrender.com/ingest/embeddings" \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"run_id": "run_123"}'
```

**Example Response:**
```json
{
  "status": "success",
  "run_id": "run_123",
  "processed": 50,
  "success": 48,
  "errors": 2
}
```

## Sample Queries

### Basic Search
```bash
# Search for cancer detection research
curl "https://your-app.onrender.com/search/publications?q=early%20cancer%20detection%20methods" \
  -H "X-API-Key: your-key"

# Search for liquid biopsy studies
curl "https://your-app.onrender.com/search/publications?q=liquid%20biopsy%20ctDNA" \
  -H "X-API-Key: your-key"
```

### Filtered Search
```bash
# High relevancy results only
curl "https://your-app.onrender.com/search/publications?q=machine%20learning%20diagnosis&min_relevancy=80" \
  -H "X-API-Key: your-key"

# Recent publications only
curl "https://your-app.onrender.com/search/publications?q=immunotherapy&date_from=2025-01-01" \
  -H "X-API-Key: your-key"

# Combined filters
curl "https://your-app.onrender.com/search/publications?q=biomarkers&min_relevancy=75&min_credibility=70&limit=50" \
  -H "X-API-Key: your-key"
```

## Integration with GitHub Actions

Add embedding generation to your pipeline after ingesting tri-model events:

```yaml
# In your GitHub Actions workflow
- name: Generate embeddings for new publications
  run: |
    curl -X POST "${{ secrets.API_URL }}/ingest/embeddings" \
      -H "X-API-Key: ${{ secrets.API_KEY }}" \
      -H "Content-Type: application/json" \
      -d '{"run_id": "${{ env.RUN_ID }}"}'
```

## Database Schema

The `publication_embeddings` table stores:

| Column | Type | Description |
|--------|------|-------------|
| publication_id | string (PK) | Unique publication identifier |
| title | text | Publication title |
| source | string | Publication source/journal |
| published_date | datetime | Publication date |
| embedded_text | text | Text that was embedded |
| embedding | vector(1536) | Embedding vector |
| embedding_model | string | Model used (text-embedding-3-small) |
| embedded_at | datetime | When embedding was generated |
| latest_run_id | string | Most recent run where pub appeared |
| final_relevancy_score | float | Cached relevancy score |
| credibility_score | float | Cached credibility score |
| final_summary | text | Cached summary |

## Troubleshooting

### "Semantic search unavailable - pgvector extension not installed"

The pgvector extension is not enabled. Run:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### "No publication embeddings available"

Run the backfill script:
```bash
python scripts/backfill_embeddings.py
```

### "SPOTITEARLY_LLM_API_KEY not configured"

Set the environment variable:
```bash
export SPOTITEARLY_LLM_API_KEY="sk-..."
```

### Slow search performance

Consider adding an IVF index for large datasets:
```sql
CREATE INDEX ON publication_embeddings
USING ivfflat (embedding vector_l2_ops)
WITH (lists = 100);
```

## GPT Integration

The `/search/publications` endpoint is designed for Custom GPT integration. The OpenAPI schema includes descriptions to help GPT understand when to use this endpoint:

- Use for conceptual queries: "dogs sniffing cancer", "liquid biopsy research"
- Use for database-wide searches across historical data
- Use when looking for related research on a topic

Example GPT instruction:
> "When the user asks about research topics or wants to find related publications, use the searchPublications endpoint with their query."
