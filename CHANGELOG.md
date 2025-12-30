# Changelog

## [1.1.0] - 2025-12-30

### Added
- **New API Endpoints** (with intelligent caching):
  - `GET /api/must-reads` - Returns must-read publications (JSON)
  - `GET /api/must-reads/md` - Returns must-read publications (Markdown)
  - `GET /api/summaries` - Returns publication summaries (JSON)
  - `GET /api/db/snapshot` - Downloads database snapshot (gzip)
  - `GET /api/artifacts/status` - Returns status of all artifacts with cache info

- **Intelligent Caching System**:
  - In-memory cache with configurable TTL (default 10 minutes)
  - Automatic fallback to stale cache if Google Drive is unavailable
  - Cache headers (`X-Cache-Status`, `X-Error`) for debugging
  - Prevents server crashes when Drive API is down
  - Returns HTTP 503 with clear message only if no cache exists

- **Configuration**:
  - New environment variable: `ACITRACK_CACHE_TTL_MINUTES` (default: 10)
  - Maintains backward compatibility with existing config

- **Documentation**:
  - Comprehensive testing instructions in README
  - curl examples for all endpoints
  - Verification checklist for Render deployment
  - Cache behavior testing guide
  - Performance testing examples

### Changed
- API version bumped to 1.1.0
- Root endpoint (`/`) now lists all available endpoints including new ones
- Support section updated to include artifacts status endpoint

### Technical Details
- Added cache management functions: `is_cache_valid()`, `get_cached_content()`, `set_cache()`
- Added `get_artifact_with_cache()` helper for unified caching behavior
- Cache structure: `{filename: {"content": bytes, "cached_at": datetime, "file_id": str}}`
- All new endpoints follow existing patterns for Drive access and error handling
- No changes to existing endpoints (backward compatible)

### Files Modified
- `main.py` - Added caching system and new endpoints
- `README.md` - Added endpoint documentation and comprehensive testing guide
- `CHANGELOG.md` - Created changelog (this file)

### Dependencies
No new dependencies required. Uses existing FastAPI and Google Drive API client.

### Deployment Notes
- No code changes required for Render deployment
- Same environment variables as before (plus optional `ACITRACK_CACHE_TTL_MINUTES`)
- New files must be present in Google Drive folder:
  - `latest_must_reads.json`
  - `latest_must_reads.md`
  - `latest_summaries.json`
  - `latest_db.sqlite.gz`

### Security
- All new endpoints are read-only
- No new authentication required (uses existing service account)
- Cache is in-memory only (not persisted to disk)
- No sensitive data exposed in error messages

---

## [1.0.0] - 2025-12-26

### Initial Release
- Basic API endpoints for AciTrack artifacts
- Google Drive integration with service account
- Shared Drive support
- Health check endpoint
- CORS configuration for OpenAI domains
