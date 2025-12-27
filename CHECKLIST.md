# Pre-Deployment Checklist

Use this checklist to ensure everything is ready before deploying to production.

## Prerequisites

### Google Cloud Setup
- [ ] Service account created
- [ ] Service account JSON downloaded
- [ ] Google Drive API enabled for project
- [ ] Service account has read access to target folder
- [ ] Folder contains required files:
  - [ ] `latest_report.md`
  - [ ] `latest_manifest.json`
  - [ ] `latest_new.csv`

### Repository Setup
- [ ] Code pushed to GitHub (or git provider)
- [ ] `.gitignore` includes service account JSON patterns
- [ ] No credentials committed to repository

### Environment Variables Ready
- [ ] `ACITRACK_DRIVE_FOLDER_ID` copied
- [ ] `ACITRACK_GCP_SA_JSON` copied (as single-line string)

---

## Local Testing

### Setup
- [ ] Virtual environment created
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] Environment variables set in `.env`

### Tests
- [ ] Server starts without errors (`python main.py`)
- [ ] Health check passes (`curl localhost:8000/health`)
- [ ] Report endpoint works (`curl localhost:8000/report`)
- [ ] Manifest endpoint works (`curl localhost:8000/manifest`)
- [ ] CSV endpoint works (`curl localhost:8000/new`)

### Verification
- [ ] No errors in terminal output
- [ ] All three files downloaded successfully
- [ ] Content looks correct

---

## Deployment (Railway)

### Account Setup
- [ ] Railway account created
- [ ] GitHub connected to Railway
- [ ] Repository authorized

### Project Configuration
- [ ] New project created
- [ ] Repository selected
- [ ] Service detected as Python app

### Environment Variables
- [ ] `ACITRACK_DRIVE_FOLDER_ID` added
- [ ] `ACITRACK_GCP_SA_JSON` added
- [ ] Variables saved

### Deployment
- [ ] Initial deployment triggered
- [ ] Build completed successfully
- [ ] Service is running
- [ ] Public URL generated

### Production Testing
- [ ] Health endpoint accessible
- [ ] Report endpoint returns data
- [ ] Manifest endpoint returns data
- [ ] CSV endpoint returns data
- [ ] Response times acceptable

---

## Deployment (Render)

### Account Setup
- [ ] Render account created
- [ ] GitHub connected to Render

### Service Configuration
- [ ] Web service created
- [ ] Repository selected
- [ ] Runtime set to Python 3
- [ ] Build command: `pip install -r requirements.txt`
- [ ] Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

### Environment Variables
- [ ] `ACITRACK_DRIVE_FOLDER_ID` added
- [ ] `ACITRACK_GCP_SA_JSON` added
- [ ] `PYTHON_VERSION` set (optional)

### Deployment
- [ ] Service created
- [ ] Initial deployment started
- [ ] Build completed
- [ ] Service is live
- [ ] URL accessible

### Production Testing
- [ ] Health endpoint accessible
- [ ] Report endpoint returns data
- [ ] Manifest endpoint returns data
- [ ] CSV endpoint returns data
- [ ] Response times acceptable

---

## Custom GPT Setup

### OpenAPI Schema
- [ ] `openapi.json` updated with production URL
- [ ] Schema validated (no JSON errors)
- [ ] File contents copied to clipboard

### GPT Configuration
- [ ] ChatGPT Plus account active
- [ ] GPT Builder accessed
- [ ] New GPT created
- [ ] Basic information filled in:
  - [ ] Name
  - [ ] Description
  - [ ] Instructions

### Action Setup
- [ ] Actions section opened
- [ ] New action created
- [ ] OpenAPI schema pasted
- [ ] Schema validated by ChatGPT
- [ ] Action saved

### Testing
- [ ] Test query sent: "Show me the latest report"
- [ ] GPT calls API successfully
- [ ] Response displayed correctly
- [ ] Test query: "Get the manifest"
- [ ] Test query: "Show new publications"

---

## Monitoring Setup

### Uptime Monitoring
- [ ] Uptime monitor account created (UptimeRobot, etc.)
- [ ] Monitor created for `/health` endpoint
- [ ] Check interval set (5 minutes recommended)
- [ ] Alert email configured
- [ ] Test alert received

### Platform Monitoring
- [ ] Platform dashboard bookmarked
- [ ] Usage alerts configured
- [ ] Cost alerts set (if applicable)
- [ ] Email notifications enabled

### Google Cloud
- [ ] Service account usage visible in console
- [ ] Drive API quota visible
- [ ] Audit logs accessible

---

## Security Review

### Credentials
- [ ] No credentials in git repository
- [ ] Service account JSON not committed
- [ ] `.env` in `.gitignore`
- [ ] Environment variables properly set in platform

### Permissions
- [ ] Service account has minimum required permissions (read-only)
- [ ] Folder shared only with service account
- [ ] No public sharing enabled

### API Security
- [ ] HTTPS enforced (automatic via platform)
- [ ] CORS configured for OpenAI domains
- [ ] Health check publicly accessible (intended)
- [ ] Data endpoints publicly accessible (intended for internal use)

### Account Security
- [ ] Deployment platform account has strong password
- [ ] 2FA enabled on deployment platform (recommended)
- [ ] 2FA enabled on Google Cloud (recommended)

---

## Documentation

### Team Documentation
- [ ] API URL documented and shared with team
- [ ] Custom GPT URL shared (if applicable)
- [ ] Access instructions provided
- [ ] Demo prepared

### Repository
- [ ] README.md reviewed
- [ ] QUICKSTART.md available
- [ ] DEPLOYMENT.md available
- [ ] SECURITY.md available

---

## Demo Preparation

### Verify Everything Works
- [ ] Test all API endpoints from browser
- [ ] Test Custom GPT with multiple queries
- [ ] Prepare backup screenshots (in case of downtime)
- [ ] Test on different network (mobile, different WiFi)

### Prepare Presentation
- [ ] API URL ready to show
- [ ] Custom GPT ready to demo
- [ ] Example queries prepared
- [ ] Backup plan if API is down

### Have Ready
- [ ] Platform dashboard open (to show logs if needed)
- [ ] API URL copied
- [ ] Custom GPT link ready
- [ ] Team briefed on how to use

---

## Post-Deployment

### Week 1
- [ ] Monitor daily for errors
- [ ] Check usage/costs
- [ ] Gather user feedback
- [ ] Fix any issues discovered

### Ongoing
- [ ] Weekly health checks
- [ ] Monthly cost review
- [ ] Quarterly security review
- [ ] Update documentation as needed

---

## Rollback Plan

If something goes wrong:

### Immediate Actions
- [ ] Check platform logs for errors
- [ ] Verify environment variables are set
- [ ] Test `/health` endpoint
- [ ] Check Google Drive folder and permissions

### If API is Down
- [ ] Use backup screenshots for demo
- [ ] Redeploy from last known good commit
- [ ] Check platform status page
- [ ] Contact platform support if needed

### If Data is Wrong
- [ ] Verify files in Google Drive
- [ ] Check file names match exactly
- [ ] Confirm service account has access
- [ ] Re-upload files if needed

---

## Sign-Off

Before going live, confirm:

- [ ] All tests passed
- [ ] Documentation complete
- [ ] Team briefed
- [ ] Monitoring configured
- [ ] Demo prepared
- [ ] Rollback plan understood

**Deployed By**: _________________

**Date**: _________________

**Production URL**: _________________

**Custom GPT URL**: _________________

---

## Quick Reference

### Production URLs
- API Base: `https://your-app.up.railway.app` or `https://your-app.onrender.com`
- Health Check: `https://your-app/health`
- Docs: `https://your-app/docs`

### Environment Variables
```bash
ACITRACK_DRIVE_FOLDER_ID=your-folder-id
ACITRACK_GCP_SA_JSON={"type":"service_account",...}
```

### Test Commands
```bash
# Health check
curl https://your-app/health

# Test endpoints
curl https://your-app/report
curl https://your-app/manifest
curl https://your-app/new
```

---

**Status**: Ready when all boxes checked ✅
