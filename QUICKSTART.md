# AciTrack Backend - Quick Start Guide

This guide gets you from zero to deployed in under 30 minutes.

## Prerequisites Checklist

Before starting, ensure you have:

- [ ] Google Cloud Service Account JSON file
- [ ] Service account has read access to your Google Drive folder
- [ ] Google Drive folder ID (from the folder URL)
- [ ] The folder contains: `latest_report.md`, `latest_manifest.json`, `latest_new.csv`
- [ ] GitHub account
- [ ] Railway or Render account (can create during deployment)

---

## 5-Minute Local Test

### Step 1: Clone and Setup (2 minutes)

```bash
# Clone repository
git clone <your-repo-url>
cd acitracker_backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
# venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Step 2: Configure Environment (1 minute)

```bash
# Copy example env file
cp .env.example .env

# Edit .env with your favorite editor
nano .env  # or vim, code, etc.
```

Set these values in `.env`:
```bash
ACITRACK_DRIVE_FOLDER_ID=your-actual-folder-id
ACITRACK_GCP_SA_JSON={"type":"service_account",...paste entire JSON...}
```

### Step 3: Run Locally (1 minute)

```bash
# Start the server
python main.py
```

You should see:
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Step 4: Test (1 minute)

Open a new terminal and run:

```bash
# Test health check
curl http://localhost:8000/health

# Test report endpoint
curl http://localhost:8000/report

# Test manifest endpoint
curl http://localhost:8000/manifest

# Test CSV endpoint
curl http://localhost:8000/new
```

If all tests pass, you're ready to deploy! 🎉

---

## 10-Minute Railway Deployment

### Step 1: Push to GitHub (2 minutes)

```bash
# Initialize git (if not already done)
git init
git add .
git commit -m "Initial commit - AciTrack Backend"

# Add your GitHub remote
git remote add origin https://github.com/yourusername/acitracker_backend.git

# Push to GitHub
git push -u origin main
```

### Step 2: Create Railway Project (3 minutes)

1. Go to [railway.app](https://railway.app)
2. Click "Login" → "Login with GitHub"
3. Click "New Project"
4. Select "Deploy from GitHub repo"
5. Authorize Railway to access your repositories
6. Select `acitracker_backend`

### Step 3: Configure Environment Variables (3 minutes)

In Railway dashboard:

1. Click on your service
2. Go to "Variables" tab
3. Click "New Variable"

Add these two variables:

**Variable 1:**
- Key: `ACITRACK_DRIVE_FOLDER_ID`
- Value: `your-folder-id-here`

**Variable 2:**
- Key: `ACITRACK_GCP_SA_JSON`
- Value: `{"type":"service_account",...}`  (paste entire JSON)

Click "Add" for each variable.

### Step 4: Deploy (2 minutes)

1. Railway automatically deploys when you add variables
2. Watch the "Deployments" tab for build progress
3. Wait for "Success" status (1-2 minutes)
4. Click "Settings" → Find your public URL
   - Example: `https://acitracker-backend-production.up.railway.app`

### Step 5: Test Production (1 minute)

```bash
# Replace with your actual Railway URL
export API_URL="https://your-app.up.railway.app"

# Test health
curl $API_URL/health

# Test endpoints
curl $API_URL/report
curl $API_URL/manifest
curl $API_URL/new
```

✅ **Deployment Complete!**

---

## 5-Minute Custom GPT Setup

### Step 1: Update OpenAPI Schema (1 minute)

1. Open `openapi.json`
2. Find the `servers` section
3. Replace the URL with your Railway URL:

```json
"servers": [
  {
    "url": "https://your-actual-app.up.railway.app",
    "description": "Production server"
  }
]
```

4. Save the file

### Step 2: Copy OpenAPI Schema (1 minute)

```bash
# Copy entire contents of openapi.json
cat openapi.json | pbcopy  # macOS
# On Linux: cat openapi.json | xclip -selection clipboard
# On Windows: type openapi.json | clip
```

### Step 3: Create Custom GPT (2 minutes)

1. Go to [ChatGPT](https://chat.openai.com)
2. Click your profile → "My GPTs"
3. Click "Create a GPT"
4. Configure the GPT:
   - **Name**: "AciTrack Assistant"
   - **Description**: "Access latest academic publication reports and data from AciTrack"
   - **Instructions**: "You help users access and analyze academic publication data from the AciTrack system. Use the available actions to fetch reports, manifests, and CSV data."

### Step 4: Add Action (1 minute)

1. Click "Configure" tab
2. Scroll to "Actions" section
3. Click "Create new action"
4. Paste your `openapi.json` contents
5. Click "Save"

### Step 5: Test Custom GPT (1 minute)

In the preview panel, ask:

```
Show me the latest report
```

The GPT should:
1. Call your API
2. Retrieve the report
3. Display it formatted

✅ **Custom GPT Ready!**

---

## Common Issues & Quick Fixes

### Issue: "Connection refused" when testing locally

**Fix**: Check that the server is running on port 8000
```bash
lsof -i :8000
```

### Issue: "File not found in Google Drive"

**Fix**: Verify files exist with exact names
- Go to your Drive folder
- Check for: `latest_report.md`, `latest_manifest.json`, `latest_new.csv`
- File names must match exactly (case-sensitive)

### Issue: "Invalid JSON in ACITRACK_GCP_SA_JSON"

**Fix**: Ensure JSON is on single line
```bash
# Convert multi-line JSON to single line
cat service-account.json | jq -c . | pbcopy
```

### Issue: Railway deployment fails

**Fix**: Check build logs
1. Railway dashboard → "Deployments" tab
2. Click failed deployment
3. Read error message
4. Common issue: Missing environment variables

### Issue: Custom GPT action fails

**Fix**: Verify server URL
1. Test your API URL directly in browser
2. Ensure URL in `openapi.json` matches your Railway URL
3. Check CORS headers are present

---

## Next Steps After Deployment

### Immediate (Do Now)

- [ ] Test all three endpoints in production
- [ ] Test Custom GPT integration
- [ ] Bookmark your Railway dashboard
- [ ] Save your API URL somewhere safe

### Within 24 Hours

- [ ] Set up uptime monitoring (UptimeRobot)
- [ ] Share API URL with team
- [ ] Test with different queries in Custom GPT
- [ ] Check Railway usage/costs

### Within 1 Week

- [ ] Monitor API performance
- [ ] Review logs for errors
- [ ] Gather user feedback
- [ ] Plan Phase 2 features (if needed)

---

## Useful Commands

### Local Development

```bash
# Activate virtual environment
source venv/bin/activate

# Install new dependency
pip install package-name
pip freeze > requirements.txt

# Run with auto-reload
uvicorn main:app --reload

# View logs
tail -f uvicorn.log
```

### Deployment

```bash
# Push changes to Railway (auto-deploys)
git add .
git commit -m "Your commit message"
git push origin main

# View Railway logs
railway logs

# Open Railway dashboard
railway open
```

### Testing

```bash
# Quick health check
curl https://your-app.up.railway.app/health | jq

# Download report
curl https://your-app.up.railway.app/report -o report.md

# Get manifest and format JSON
curl https://your-app.up.railway.app/manifest | jq .

# Download CSV
curl https://your-app.up.railway.app/new -o new.csv
```

---

## Support Checklist

Before asking for help:

1. [ ] Check `/health` endpoint
2. [ ] Review Railway deployment logs
3. [ ] Verify environment variables are set
4. [ ] Test locally with same env vars
5. [ ] Check DEPLOYMENT.md troubleshooting section
6. [ ] Review Railway/Render platform status

---

## Resource Links

- **Documentation**: README.md
- **Deployment Guide**: DEPLOYMENT.md
- **Security**: SECURITY.md
- **Railway Docs**: https://docs.railway.app
- **FastAPI Docs**: https://fastapi.tiangolo.com
- **Google Drive API**: https://developers.google.com/drive

---

## Summary

**Total Time**: ~20 minutes
- Local test: 5 minutes
- Railway deployment: 10 minutes
- Custom GPT setup: 5 minutes

**What You Have Now**:
- ✅ Working API deployed to production
- ✅ HTTPS endpoint accessible globally
- ✅ Custom GPT integrated and functional
- ✅ Monitoring and health checks ready

**You're Done!** 🎉

Now you can demo AciTrack to your team using the Custom GPT.
