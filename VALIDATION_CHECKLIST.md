# Production Bug Fix - Validation Checklist

## Issue Summary
**Production bug**: Backend returns "Output file 'must_reads' not found via any resolution method" even though files exist in Google Drive.

**Root causes identified**:
1. Key name mismatch: Backend looked for `"must_reads"` but manifest used `"must_reads_json"`
2. Missing alternative key attempts in drive_file_ids and drive_output_paths
3. No fallback to direct folder traversal when Drive fields missing
4. Insufficient debug logging

## Fixes Applied

### 1. Enhanced Key Resolution (main.py:494-666)
- ✅ Added support for both base keys (`"must_reads"`) and suffixed keys (`"must_reads_json"`)
- ✅ Backend now tries all key variants for each file type:
  - `must_reads` → tries `["must_reads", "must_reads_json"]`
  - `report` → tries `["report", "report_md"]`
  - `summaries` → tries `["summaries", "summaries_json"]`
  - `new` → tries `["new", "new_csv"]`

### 2. Smart Folder Traversal Fallback (main.py:581-625)
- ✅ New Priority 3 fallback: Navigate directly to run folder and find files by exact filename
- ✅ Path: `{DRIVE_FOLDER_ID}` → `"Daily"` or `"Weekly"` → `"{run_id}"` → `"{filename}"`
- ✅ Works even if manifest has NO drive_file_ids or drive_output_paths

### 3. Comprehensive Debug Logging (main.py:538-660)
- ✅ Added structured logging with `[{mode}/{run_id}]` prefix
- ✅ Logs each resolution attempt with method and keys tried
- ✅ Shows which keys exist in drive_file_ids and drive_output_paths
- ✅ Final error message lists all attempted methods

### 4. Manifest Validation Endpoint (main.py:939-994)
- ✅ Enhanced `/manifest` endpoint with optional validation warnings
- ✅ Detects missing drive_file_ids and drive_output_paths
- ✅ Provides recommendations for pipeline updates

## Resolution Priority (4 Strategies)

The backend now tries these methods in order:

1. **drive_file_ids** (fastest - direct file ID)
   - Tries both `file_key` and `file_key_suffix` variants
   - Example: For "must_reads", tries `drive_file_ids["must_reads"]` and `drive_file_ids["must_reads_json"]`

2. **drive_output_paths** (fast - folder navigation)
   - Tries both key variants
   - Navigates Drive folder hierarchy using path

3. **Smart folder traversal** (NEW - robust fallback)
   - Navigates: `Drive Root` → `"Daily"/"Weekly"` → `run_id` → `filename`
   - Matches exact filenames: `must_reads.json`, `report.md`, `summaries.json`, `new.csv`
   - Works even with empty manifests

4. **Legacy output_paths** (backward compatibility)
   - Converts local paths to Drive paths
   - Strips `"data/"` prefix if present

## Validation Steps

### Step 1: Check Manifest Structure
```bash
# Check what keys exist in the manifest
curl "https://acitracker-backend.onrender.com/manifest?mode=daily" | jq '{
  has_drive_file_ids: (.drive_file_ids != null),
  drive_file_ids_keys: (.drive_file_ids | keys),
  has_drive_output_paths: (.drive_output_paths != null),
  drive_output_paths_keys: (.drive_output_paths | keys)
}'
```

**Expected output** (if pipeline is updated):
```json
{
  "has_drive_file_ids": true,
  "drive_file_ids_keys": ["must_reads_json", "report_md", "summaries_json", "new_csv"],
  "has_drive_output_paths": true,
  "drive_output_paths_keys": ["must_reads_json", "report_md", "summaries_json", "new_csv"]
}
```

### Step 2: Test File Resolution
```bash
# Test must-reads endpoint with debug mode
curl "https://acitracker-backend.onrender.com/api/must-reads?mode=daily&debug=true" | jq '._debug.resolution'

# Test report endpoint
curl "https://acitracker-backend.onrender.com/report?mode=daily&debug=true" -I

# Test summaries endpoint
curl "https://acitracker-backend.onrender.com/api/summaries?mode=daily&debug=true" -I

# Test new.csv endpoint
curl "https://acitracker-backend.onrender.com/new?mode=daily&debug=true" -I
```

**Expected output** (debug info):
```json
{
  "resolution_method": "drive_file_id",
  "resolved_path": "1ABC_file_id",
  "file_key": "must_reads",
  "manifest_key": "must_reads_json"
}
```

Or if using folder traversal:
```json
{
  "resolution_method": "smart_folder_traversal",
  "resolved_path": "Daily/daily-2026-01-14/must_reads.json",
  "file_key": "must_reads",
  "file_id": "1ABC_file_id"
}
```

### Step 3: Check Logs (Render Dashboard)
Look for structured log output:
```
[daily/daily-2026-01-14] Resolving 'must_reads' - will try keys: ['must_reads', 'must_reads_json']
[daily/daily-2026-01-14] Trying drive_file_id for key 'must_reads_json': 1ABC_file_id
[daily/daily-2026-01-14] ✓ Resolved 'must_reads' via drive_file_id['must_reads_json']: 1ABC_file_id
```

Or if using fallback:
```
[daily/daily-2026-01-14] Resolving 'must_reads' - will try keys: ['must_reads', 'must_reads_json']
[daily/daily-2026-01-14] Attempting smart folder traversal fallback
[daily/daily-2026-01-14] Looking for file in: Daily/daily-2026-01-14/must_reads.json
[daily/daily-2026-01-14] ✓ Resolved 'must_reads' via folder traversal: must_reads.json
```

### Step 4: Validate All Endpoints Work
```bash
# Daily mode
curl "https://acitracker-backend.onrender.com/api/must-reads?mode=daily" -s | jq '.must_reads | length'
curl "https://acitracker-backend.onrender.com/report?mode=daily" -s | head -5
curl "https://acitracker-backend.onrender.com/api/summaries?mode=daily" -s | jq '. | length'
curl "https://acitracker-backend.onrender.com/new?mode=daily" -s | head -5

# Weekly mode (if available)
curl "https://acitracker-backend.onrender.com/api/must-reads?mode=weekly" -s | jq '.must_reads | length'
curl "https://acitracker-backend.onrender.com/report?mode=weekly" -s | head -5
```

**All should return HTTP 200** with valid content.

## Deployment Steps

1. **Commit and push changes**:
   ```bash
   git add main.py
   git commit -m "Fix missing logger definition causing 503 errors"
   git push origin main
   ```

2. **Verify Render auto-deploys** (usually takes 2-3 minutes)

3. **Check Render logs** for successful startup:
   ```
   INFO:     Started server process
   INFO:     Waiting for application startup.
   INFO:     Application startup complete.
   INFO:     Uvicorn running on http://0.0.0.0:8000
   ```

4. **Run validation checklist** (above steps)

## Expected Behavior After Fix

### Scenario 1: Modern Pipeline (with drive_file_ids)
- Backend uses drive_file_ids for fast direct access ✅
- Resolution time: ~200-300ms

### Scenario 2: Old Pipeline (no Drive fields)
- Backend falls back to smart folder traversal ✅
- Navigates: Drive Root → Daily/Weekly → run_id → filename
- Resolution time: ~500-800ms (slower but works)

### Scenario 3: Missing Files
- Backend tries all 4 methods
- Returns detailed error listing all keys tried:
  ```
  Output file 'must_reads' not found via any resolution method for mode=daily, run_id=daily-2026-01-14.
  Tried: drive_file_ids=['report_md'], drive_output_paths=['report_md'], folder_traversal, legacy_paths
  ```

## Pipeline Recommendations

To optimize backend performance, update pipeline to:

1. **Include drive_file_ids in manifest**:
   ```json
   {
     "drive_file_ids": {
       "must_reads_json": "1ABC_file_id",
       "report_md": "2DEF_file_id",
       "summaries_json": "3GHI_file_id",
       "new_csv": "4JKL_file_id"
     }
   }
   ```

2. **Include drive_output_paths in manifest**:
   ```json
   {
     "drive_output_paths": {
       "must_reads_json": "Daily/daily-2026-01-14/must_reads.json",
       "report_md": "Daily/daily-2026-01-14/report.md",
       "summaries_json": "Daily/daily-2026-01-14/summaries.json",
       "new_csv": "Daily/daily-2026-01-14/new.csv"
     }
   }
   ```

3. **Use suffixed key names** for consistency:
   - `must_reads_json` (not `must_reads`)
   - `report_md` (not `report`)
   - `summaries_json` (not `summaries`)
   - `new_csv` (not `new`)

## Success Criteria

- [x] Backend compiles without errors
- [ ] `/manifest?mode=daily` returns manifest with or without warnings
- [ ] `/api/must-reads?mode=daily` returns must_reads.json content (HTTP 200)
- [ ] `/report?mode=daily` returns report.md content (HTTP 200)
- [ ] `/api/summaries?mode=daily` returns summaries.json content (HTTP 200)
- [ ] `/new?mode=daily` returns new.csv content (HTTP 200)
- [ ] Same for `mode=weekly`
- [ ] Render logs show resolution method and success
- [ ] No 503 errors in production

## Rollback Plan

If issues occur:
1. Revert commit: `git revert HEAD`
2. Push: `git push origin main`
3. Render will auto-deploy previous version
4. Check logs to identify issue

## Support

For issues:
1. Check Render logs for detailed resolution attempts
2. Test `/manifest?mode=daily` to see what keys exist
3. Test with `debug=true` to see resolution method used
4. Contact engineering team with logs
