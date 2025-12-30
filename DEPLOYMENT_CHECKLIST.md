# Deployment Checklist for v1.1.0

Use this checklist when deploying the updated AciTracker backend to Render.

## Pre-Deployment

### 1. Verify Google Drive Files
Ensure all required files exist in your Google Drive folder:

- [ ] `latest_report.md` (existing)
- [ ] `latest_manifest.json` (existing)
- [ ] `latest_new.csv` (existing)
- [ ] `latest_must_reads.json` (NEW)
- [ ] `latest_must_reads.md` (NEW)
- [ ] `latest_summaries.json` (NEW)
- [ ] `latest_db.sqlite.gz` (NEW)

**How to check:**
```bash
# Use Google Drive web UI or run a quick test locally
curl http://localhost:8000/api/artifacts/status | jq '.artifacts'
```

### 2. Test Locally First
- [ ] Clone/pull latest code
- [ ] Create virtual environment: `python -m venv venv && source venv/bin/activate`
- [ ] Install dependencies: `pip install -r requirements.txt`
- [ ] Set environment variables (see below)
- [ ] Run server: `python main.py`
- [ ] Test all endpoints (see Testing section in README)

**Required Environment Variables for Local Testing:**
```bash
export ACITRACK_DRIVE_FOLDER_ID="your-folder-id"
export ACITRACK_GCP_SA_JSON='{"type":"service_account",...}'
# Optional:
export ACITRACK_CACHE_TTL_MINUTES="10"
```

## Deployment to Render

### 3. Update Render Service
- [ ] Push code to GitHub main branch
- [ ] Render will auto-deploy (if auto-deploy enabled)
- [ ] OR manually trigger deploy in Render dashboard

### 4. Verify Environment Variables in Render
Check that these are set in Render dashboard → Environment:

- [ ] `ACITRACK_DRIVE_FOLDER_ID` (should already exist)
- [ ] `ACITRACK_GCP_SA_JSON` (should already exist)
- [ ] `ACITRACK_CACHE_TTL_MINUTES` (OPTIONAL, defaults to 10 if not set)
- [ ] `PORT` (auto-set by Render, don't modify)

### 5. Monitor Deployment
- [ ] Watch deployment logs in Render dashboard
- [ ] Wait for "Build successful" and "Service is live"
- [ ] Check for any startup errors

## Post-Deployment Verification

### 6. Test Health Endpoints
```bash
# Replace with your actual Render URL
DEPLOY_URL="https://your-app.onrender.com"

# Test health
curl $DEPLOY_URL/health
# Should return: {"status": "healthy", ...}

# Test artifacts status
curl $DEPLOY_URL/api/artifacts/status | jq .
# Should show all artifacts as available
```

### 7. Test Original Endpoints (Regression Test)
```bash
# Ensure existing endpoints still work
curl $DEPLOY_URL/report
curl $DEPLOY_URL/manifest | jq .
curl $DEPLOY_URL/new
```

### 8. Test New Endpoints
```bash
# Test must-reads JSON
curl $DEPLOY_URL/api/must-reads | jq .

# Test must-reads Markdown
curl -I $DEPLOY_URL/api/must-reads/md
# Should see Content-Type: text/markdown

# Test summaries
curl $DEPLOY_URL/api/summaries | jq .

# Test database snapshot
curl -o test_db.sqlite.gz $DEPLOY_URL/api/db/snapshot
gunzip -t test_db.sqlite.gz
rm test_db.sqlite.gz
```

### 9. Test Caching Behavior
```bash
# First request (cache miss)
time curl -s $DEPLOY_URL/api/must-reads > /dev/null

# Second request (cache hit, should be faster)
time curl -s $DEPLOY_URL/api/must-reads > /dev/null

# Check cache status
curl $DEPLOY_URL/api/artifacts/status | jq '.artifacts.must_reads_json.cache'
# Should show: {"cached": true, "is_valid": true, ...}
```

### 10. Verify Content-Type Headers
```bash
# Check all Content-Type headers are correct
curl -I $DEPLOY_URL/api/must-reads | grep "content-type"
# Should be: application/json

curl -I $DEPLOY_URL/api/must-reads/md | grep "content-type"
# Should be: text/markdown

curl -I $DEPLOY_URL/api/summaries | grep "content-type"
# Should be: application/json

curl -I $DEPLOY_URL/api/db/snapshot | grep -E "content-type|content-disposition"
# Should be: application/gzip
# Should have: attachment; filename=latest_db.sqlite.gz
```

## Final Checklist

- [ ] All health checks pass
- [ ] All original endpoints still work (no regressions)
- [ ] All new endpoints return correct data
- [ ] All Content-Type headers are correct
- [ ] Caching is working (second request faster)
- [ ] Database snapshot downloads and is valid gzip
- [ ] Artifacts status endpoint shows all files available
- [ ] No errors in Render logs

## Rollback Plan

If deployment fails:

1. **Quick Rollback via Render:**
   - Go to Render dashboard → Deploys
   - Find previous successful deploy
   - Click "Redeploy" on that version

2. **Or revert Git commit:**
   ```bash
   git revert HEAD
   git push origin main
   # Render will auto-deploy the reverted version
   ```

## Custom GPT Integration

After successful deployment:

1. [ ] Update Custom GPT Actions with new endpoints:
   - Add `/api/must-reads` operation
   - Add `/api/must-reads/md` operation
   - Add `/api/summaries` operation
   - Add `/api/db/snapshot` operation (if needed)

2. [ ] Test Custom GPT with new endpoints:
   - Ask: "What are the must-read publications?"
   - Ask: "Show me publication summaries"
   - Ask: "Download the database snapshot"

## Monitoring

Set up monitoring for the new endpoints:

- [ ] Add `/api/artifacts/status` to uptime monitoring
- [ ] Monitor response times for cached endpoints
- [ ] Set up alerts for cache failures (check logs for "stale cache" warnings)

## Notes

- Cache is in-memory only, so restarts will clear cache
- First request after restart will be slower (cache miss)
- Stale cache fallback prevents 503 errors when Drive is temporarily unavailable
- Cache TTL default is 10 minutes, adjust via `ACITRACK_CACHE_TTL_MINUTES` if needed

---

**Deployment Date:** _______________

**Deployed By:** _______________

**Render URL:** _______________

**Status:** ⬜ Success  ⬜ Failed  ⬜ Rolled Back

**Notes:**
