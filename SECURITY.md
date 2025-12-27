# Security Documentation

This document outlines the security considerations, limitations, and best practices for the AciTrack Backend API.

---

## Current Security Model

### Authentication

**Status**: ✅ **Implemented - Service Account Based**

- The API uses Google Cloud Service Account authentication
- Service account credentials stored as environment variable
- Read-only access to Google Drive
- No per-user authentication required

**Implications**:
- Anyone with the API URL can access the data
- Suitable for internal SpotItEarly employee use
- Data is considered "internal but not sensitive"

### Authorization

**Status**: ⚠️ **Public Access**

- No authorization layer implemented
- All endpoints are publicly accessible
- No API keys or tokens required

**Why this is acceptable**:
- Academic publication data is intended for internal sharing
- Custom GPT is for SpotItEarly employees only
- Data is not sensitive or confidential
- Faster time-to-market for demo

### CORS (Cross-Origin Resource Sharing)

**Status**: ✅ **Configured for OpenAI**

```python
allow_origins=["https://chat.openai.com", "https://chatgpt.com"]
```

- Restricts browser-based access to OpenAI domains
- Prevents unauthorized web applications from accessing the API
- Note: CORS only protects browser-based requests, not server-to-server calls

---

## Current Limitations

### 1. No Rate Limiting

**Risk**: Potential abuse or excessive API calls

**Mitigation Options** (not implemented):
- Add rate limiting middleware (e.g., `slowapi`)
- Set quotas at the deployment platform level
- Monitor usage and add limits if needed

**When to implement**: If you see unexpected traffic or costs

### 2. No API Key Authentication

**Risk**: Anyone with the URL can access endpoints

**Mitigation Options** (not implemented):
- Add API key header validation
- Implement OAuth 2.0
- Use API gateway with authentication

**When to implement**: Before making API public or if data becomes sensitive

### 3. No Request Logging

**Risk**: Difficult to audit access or detect abuse

**Current State**: Platform-level logs (Railway/Render) only

**Enhancement Options**:
- Add structured logging with request IDs
- Log IP addresses, timestamps, endpoints accessed
- Integrate with logging service (e.g., Logtail, Datadog)

**When to implement**: For compliance or audit requirements

### 4. Service Account Credentials in Environment

**Risk**: If platform is compromised, credentials could be exposed

**Current Mitigation**:
- Credentials stored as environment variables (not in code)
- Service account has minimal permissions (read-only Drive access)
- `.gitignore` prevents accidental commit

**Best Practice**:
- For production: Use secret management service (Railway Secrets, Render environment groups)
- Rotate service account keys periodically
- Monitor service account usage in Google Cloud Console

### 5. No Input Validation

**Risk**: Not applicable for current endpoints (no user input)

**Note**: All current endpoints are GET requests with no parameters. If you add query parameters or POST endpoints in the future, implement input validation.

---

## Data Security

### Data in Transit

✅ **HTTPS enforced** by Railway/Render
- All communication encrypted with TLS 1.2+
- Certificate management handled by platform

### Data at Rest

✅ **Google Drive security**
- Files stored in Google Drive with enterprise-grade security
- Access controlled by service account permissions
- Google's encryption at rest

### Data Sensitivity

Current data classification: **Internal - Non-Sensitive**

- Academic publication metadata (public information)
- Reports and manifests for internal use
- No personal data, credentials, or business secrets

---

## Google Cloud Security

### Service Account Best Practices

✅ **Implemented**:
- Service account has read-only scope: `https://www.googleapis.com/auth/drive.readonly`
- Access limited to specific folder (not entire Drive)
- Credentials not committed to git

⚠️ **Recommended**:
- Create a dedicated service account for this API only
- Name it clearly: `acitrack-api@project.iam.gserviceaccount.com`
- Document which services use this account
- Set up alerts for unusual activity in Google Cloud Console

### Folder Permissions

✅ **Recommended Setup**:
1. Create a dedicated folder for AciTrack outputs
2. Share folder with service account email (viewer access)
3. Do not share folder publicly
4. Regularly audit folder permissions

---

## Deployment Platform Security

### Railway Security

✅ **Built-in protections**:
- Environment variables encrypted at rest
- HTTPS/TLS automatic
- DDoS protection
- Isolated deployment environments

**Recommended**:
- Enable 2FA on Railway account
- Limit team member access
- Use Railway's audit logs

### Render Security

✅ **Built-in protections**:
- Environment variables encrypted
- Automatic HTTPS
- DDoS protection
- Zero-trust network

**Recommended**:
- Enable 2FA on Render account
- Use environment groups for shared secrets
- Review access logs regularly

---

## Threat Model

### Potential Threats

| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|------------|
| Unauthorized data access | Medium | Low | CORS + internal use only |
| API abuse / DoS | Low | Medium | Monitor usage, add rate limiting if needed |
| Service account compromise | Low | Medium | Read-only permissions, rotate keys |
| Data exposure in logs | Low | Low | No sensitive data in responses |
| MITM attacks | Very Low | Low | HTTPS enforced |

### Attack Scenarios

**Scenario 1: URL is leaked publicly**
- **Impact**: Anyone can read your reports/data
- **Mitigation**: Data is not sensitive; can add API key if needed
- **Response**: Rotate URL by redeploying; add authentication

**Scenario 2: Service account key is exposed**
- **Impact**: Attacker can read files in the Drive folder
- **Mitigation**: Read-only access limits damage
- **Response**: Revoke key, create new service account, update env vars

**Scenario 3: Excessive API calls**
- **Impact**: Higher costs, potential service degradation
- **Mitigation**: Monitor usage via platform
- **Response**: Add rate limiting, investigate source

---

## Compliance Considerations

### GDPR

**Status**: Not applicable
- No personal data collected or processed
- No EU citizens' data
- If this changes: Implement privacy policy, data retention, right to erasure

### SOC 2

**Status**: Not applicable
- For internal use only
- If pursuing SOC 2: Add access logs, audit trails, encryption key management

---

## Future Security Enhancements

### Phase 1: Enhanced Monitoring (1-2 hours)

```python
# Add structured logging
import logging
from datetime import datetime

logger = logging.getLogger("acitrack")

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = datetime.utcnow()
    response = await call_next(request)
    duration = (datetime.utcnow() - start_time).total_seconds()

    logger.info(
        f"path={request.url.path} method={request.method} "
        f"status={response.status_code} duration={duration}s"
    )

    return response
```

### Phase 2: API Key Authentication (2-3 hours)

```python
from fastapi import Security, HTTPException
from fastapi.security import APIKeyHeader

API_KEY = os.getenv("ACITRACK_API_KEY")
api_key_header = APIKeyHeader(name="X-API-Key")

def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return api_key

@app.get("/report", dependencies=[Depends(verify_api_key)])
async def get_report():
    # ... existing code
```

### Phase 3: Rate Limiting (1-2 hours)

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.get("/report")
@limiter.limit("10/minute")
async def get_report(request: Request):
    # ... existing code
```

### Phase 4: Caching (2-3 hours)

```python
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache

@app.on_event("startup")
async def startup():
    FastAPICache.init(InMemoryBackend())

@app.get("/report")
@cache(expire=300)  # Cache for 5 minutes
async def get_report():
    # ... existing code
```

**Benefits**:
- Reduce Google Drive API calls
- Faster response times
- Lower costs
- Reduced rate limit risk

---

## Security Checklist

Before going to production, verify:

- [ ] Service account has minimal permissions (read-only)
- [ ] Service account JSON is not in git repository
- [ ] Environment variables are set correctly
- [ ] HTTPS is enforced (handled by platform)
- [ ] CORS is configured for OpenAI only
- [ ] No sensitive data in API responses
- [ ] API URL is shared only with authorized users
- [ ] Deployment platform account has 2FA enabled
- [ ] Google Cloud Console monitoring is enabled
- [ ] Uptime monitoring is configured
- [ ] Team knows how to respond to security incidents

---

## Incident Response

### If Service Account is Compromised

1. **Immediate Actions**:
   - Revoke the service account key in Google Cloud Console
   - Create a new service account
   - Generate new key
   - Update `ACITRACK_GCP_SA_JSON` environment variable
   - Redeploy application

2. **Investigation**:
   - Check Google Cloud audit logs
   - Review Drive folder access logs
   - Identify what data was accessed

3. **Prevention**:
   - Rotate keys quarterly
   - Limit key lifetime
   - Monitor for unusual access patterns

### If API is Being Abused

1. **Immediate Actions**:
   - Check Railway/Render logs for traffic patterns
   - Identify source IPs
   - If severe: Temporarily disable service

2. **Mitigation**:
   - Implement rate limiting
   - Add IP allowlist if needed
   - Consider adding API key authentication

3. **Prevention**:
   - Set up usage alerts
   - Monitor costs daily
   - Implement rate limiting proactively

---

## Questions & Answers

**Q: Is this secure enough for production?**
A: For internal use with non-sensitive data: Yes. For public use or sensitive data: Add authentication first.

**Q: Should we add authentication now?**
A: Not necessary for initial demo. Add when:
- API URL has been leaked
- Usage exceeds expectations
- Data becomes sensitive
- Required for compliance

**Q: What's the biggest security risk?**
A: URL leakage leading to unauthorized access. Mitigation: Keep URL internal; monitor usage.

**Q: How do we know if we're being attacked?**
A: Set up monitoring:
- Railway/Render usage alerts
- Uptime monitoring with response time tracking
- Google Cloud audit logs

**Q: Can someone delete our files?**
A: No. Service account has read-only access. Even if compromised, attacker cannot modify or delete files.

---

## Security Contact

For security concerns or to report vulnerabilities:
- Review deployment platform logs first
- Check Google Cloud Console for suspicious activity
- Contact your team lead or platform administrator

---

## Summary

### ✅ What's Secure

- HTTPS encryption for all traffic
- Service account with read-only access
- Environment variable storage (not in code)
- CORS protection for browser requests
- Platform-level DDoS protection

### ⚠️ What's Not Implemented

- API key authentication
- Rate limiting
- Access logging
- Request monitoring
- Caching

### 📋 Recommended Next Steps

1. **Week 1**: Monitor usage and costs
2. **Week 2**: Add structured logging
3. **Week 3**: Implement caching if needed
4. **Week 4**: Add rate limiting if traffic is high
5. **Month 2**: Consider API key authentication for production

**For the demo next week**: Current security is sufficient for internal use with non-sensitive data.
