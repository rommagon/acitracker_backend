# AciTrack Backend - Deployment Guide

This guide provides step-by-step instructions for deploying the AciTrack backend API to Railway or Render.

## Prerequisites

Before deployment, ensure you have:

1. **Google Drive Service Account JSON**
   - The service account JSON credentials
   - Service account has read access to the Google Drive folder
   - The folder ID where files are stored

2. **Repository Access**
   - This repository pushed to GitHub (or other git provider)

---

## Option 1: Deploy to Railway (Recommended)

Railway offers simple deployment with automatic HTTPS and environment variable management.

### Step 1: Create Railway Account

1. Go to [railway.app](https://railway.app)
2. Sign up with GitHub
3. Verify your email

### Step 2: Create New Project

1. Click "New Project"
2. Select "Deploy from GitHub repo"
3. Authorize Railway to access your GitHub
4. Select the `acitracker_backend` repository

### Step 3: Configure Environment Variables

1. In your Railway project, click on the service
2. Go to "Variables" tab
3. Add the following environment variables:

   **ACITRACK_DRIVE_FOLDER_ID**
   ```
   <your-google-drive-folder-id>
   ```

   **ACITRACK_GCP_SA_JSON**
   ```json
   {"type":"service_account","project_id":"...","private_key_id":"...","private_key":"...","client_email":"...","client_id":"...","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_x509_cert_url":"..."}
   ```

   **Note**: Paste the entire service account JSON as a single-line string.

   **PORT** (optional, Railway auto-sets this)
   ```
   8000
   ```

### Step 4: Configure Start Command

Railway should auto-detect the Python app, but to ensure correct startup:

1. In "Settings" tab, scroll to "Start Command"
2. Set to:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port $PORT
   ```

### Step 5: Deploy

1. Railway will automatically deploy when you push to your main branch
2. Wait for deployment to complete (watch the deployment logs)
3. Once deployed, Railway will provide a public URL like:
   ```
   https://acitracker-backend-production.up.railway.app
   ```

### Step 6: Verify Deployment

Test your endpoints:

```bash
# Health check
curl https://your-app.up.railway.app/health

# Get report
curl https://your-app.up.railway.app/report

# Get manifest
curl https://your-app.up.railway.app/manifest

# Get new publications
curl https://your-app.up.railway.app/new
```

### Step 7: Update OpenAPI Schema

1. Copy your Railway URL
2. Open `openapi.json`
3. Update the `servers` section:
   ```json
   "servers": [
     {
       "url": "https://your-actual-app.up.railway.app",
       "description": "Production server"
     }
   ]
   ```

---

## Option 2: Deploy to Render

Render is another excellent option with a generous free tier.

### Step 1: Create Render Account

1. Go to [render.com](https://render.com)
2. Sign up with GitHub
3. Verify your email

### Step 2: Create New Web Service

1. Click "New +" → "Web Service"
2. Connect your GitHub repository
3. Select the `acitracker_backend` repository

### Step 3: Configure Service

Fill in the following settings:

- **Name**: `acitracker-backend`
- **Region**: Choose closest to your users
- **Branch**: `main`
- **Runtime**: `Python 3`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

### Step 4: Configure Environment Variables

In the "Environment" section, add:

1. **ACITRACK_DRIVE_FOLDER_ID**
   - Value: `<your-google-drive-folder-id>`

2. **ACITRACK_GCP_SA_JSON**
   - Value: `{"type":"service_account",...}` (entire JSON as single line)

3. **PYTHON_VERSION** (optional)
   - Value: `3.11.0`

### Step 5: Deploy

1. Click "Create Web Service"
2. Render will build and deploy your application
3. Wait for deployment (initial deploy takes 3-5 minutes)
4. Your service will be available at:
   ```
   https://acitracker-backend.onrender.com
   ```

### Step 6: Verify Deployment

Test your endpoints:

```bash
# Health check
curl https://acitracker-backend.onrender.com/health

# Get report
curl https://acitracker-backend.onrender.com/report
```

### Step 7: Update OpenAPI Schema

Update `openapi.json` with your Render URL:

```json
"servers": [
  {
    "url": "https://acitracker-backend.onrender.com",
    "description": "Production server"
  }
]
```

---

## Post-Deployment Configuration

### Configure Custom GPT Action

1. Go to [ChatGPT](https://chat.openai.com)
2. Navigate to GPT Builder
3. Create or edit your Custom GPT
4. Go to "Actions" section
5. Click "Create new action"
6. Paste the contents of `openapi.json` (with your updated server URL)
7. Click "Save"

### Test in Custom GPT

Ask your Custom GPT:
- "Show me the latest report"
- "What new publications are available?"
- "Get the manifest"

The GPT should automatically call your API endpoints.

---

## Monitoring & Maintenance

### Railway

- **Logs**: View in Railway dashboard → Your service → "Deployments" → Click deployment
- **Metrics**: View CPU, memory, network usage in "Metrics" tab
- **Cost**: Check usage in "Usage" tab

### Render

- **Logs**: Available in service dashboard under "Logs" tab
- **Metrics**: View in "Metrics" tab
- **Cost**: Free tier includes 750 hours/month

### Health Monitoring

Set up a free uptime monitor with:
- [UptimeRobot](https://uptimerobot.com)
- [Better Uptime](https://betteruptime.com)

Configure to ping: `https://your-app-url/health` every 5 minutes.

---

## Troubleshooting

### Issue: "ACITRACK_GCP_SA_JSON environment variable not set"

**Solution**: Ensure the service account JSON is properly set in environment variables. It must be valid JSON as a single-line string.

### Issue: "File not found in Google Drive folder"

**Solution**:
1. Verify the folder ID is correct
2. Check that files exist in the folder with exact names:
   - `latest_report.md`
   - `latest_manifest.json`
   - `latest_new.csv`
3. Ensure service account has read permissions on the folder

### Issue: "Google Drive API error: 403"

**Solution**: The service account needs read access to the folder. Share the folder with the service account email address.

### Issue: Service is slow on first request

**Solution**: This is normal for free tier deployments (cold starts). The service "spins down" after inactivity and takes 10-30 seconds to wake up. Consider upgrading to a paid plan for always-on instances.

### Issue: CORS errors in Custom GPT

**Solution**: The API already includes CORS headers for OpenAI domains. If you see CORS errors:
1. Check that your server URL is correct in the OpenAPI schema
2. Verify the API is returning the correct `Access-Control-Allow-Origin` headers

---

## Scaling Considerations

### Free Tier Limits

**Railway**:
- $5 free credit per month
- 500 hours of usage
- 512MB RAM, 1GB storage

**Render**:
- 750 hours/month free
- 512MB RAM
- Service spins down after 15 minutes of inactivity

### When to Upgrade

Consider upgrading when:
- Your Custom GPT has many users (>100/day)
- You need guaranteed uptime
- Cold starts are unacceptable
- You need more than 512MB RAM

### Production Recommendations

For a production environment:
1. **Upgrade to paid tier** ($7-10/month)
2. **Enable auto-scaling** if available
3. **Set up monitoring** with alerts
4. **Implement caching** (Redis) to reduce Drive API calls
5. **Add rate limiting** to prevent abuse
6. **Use a custom domain** for professional appearance

---

## Environment Variable Reference

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `ACITRACK_DRIVE_FOLDER_ID` | Yes | Google Drive folder ID containing files | `1a2b3c4d5e6f7g8h9i0j` |
| `ACITRACK_GCP_SA_JSON` | Yes | Service account JSON credentials (as string) | `{"type":"service_account",...}` |
| `PORT` | No | Port to run server on (auto-set by platform) | `8000` |

---

## Security Notes

See `SECURITY.md` for comprehensive security documentation.

---

## Support

For issues or questions:
1. Check Railway/Render logs for errors
2. Verify environment variables are set correctly
3. Test the `/health` endpoint
4. Review the troubleshooting section above

---

## Next Steps

After successful deployment:

1. ✅ Test all three endpoints (`/report`, `/manifest`, `/new`)
2. ✅ Configure Custom GPT with OpenAPI schema
3. ✅ Test Custom GPT integration
4. ✅ Set up uptime monitoring
5. ✅ Document API URL for team use
6. 📊 Monitor usage and costs
7. 🔄 Set up automatic deployments on git push
