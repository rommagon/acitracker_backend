"""
AciTrack Backend API
A FastAPI service that exposes academic publication data from Google Drive
for consumption by Custom GPT Actions.
"""

import os
import json
import tempfile
from typing import Optional, Dict, Any
from io import BytesIO
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

# Initialize FastAPI app
app = FastAPI(
    title="AciTrack API",
    description="API for accessing latest academic publication reports from Google Drive",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add CORS middleware to allow Custom GPT access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://chat.openai.com", "https://chatgpt.com"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Configuration from environment variables
DRIVE_FOLDER_ID = os.getenv("ACITRACK_DRIVE_FOLDER_ID")
GCP_SA_JSON = os.getenv("ACITRACK_GCP_SA_JSON")

# File names to search for in Google Drive
FILE_NAMES = {
    "report": "latest_report.md",
    "manifest": "latest_manifest.json",
    "new": "latest_new.csv",
    "must_reads_json": "latest_must_reads.json",
    "must_reads_md": "latest_must_reads.md",
    "summaries": "latest_summaries.json",
    "db_snapshot": "latest_db.sqlite.gz"
}

# Global Drive service instance and drive ID cache
_drive_service = None
_drive_id = None

# In-memory cache for artifacts
# Structure: {filename: {"content": bytes, "cached_at": datetime, "file_id": str}}
_artifact_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_MINUTES = int(os.getenv("ACITRACK_CACHE_TTL_MINUTES", "10"))  # Default 10 minutes


def get_drive_service():
    """
    Initialize and return Google Drive service using service account credentials.
    Uses a singleton pattern to avoid recreating the service on each request.
    Supports Shared Drives (formerly Team Drives).
    """
    global _drive_service

    if _drive_service is not None:
        return _drive_service

    if not GCP_SA_JSON:
        raise ValueError("ACITRACK_GCP_SA_JSON environment variable not set")

    if not DRIVE_FOLDER_ID:
        raise ValueError("ACITRACK_DRIVE_FOLDER_ID environment variable not set")

    try:
        # Parse service account JSON from environment variable
        sa_info = json.loads(GCP_SA_JSON)

        # Create credentials with Drive scope
        credentials = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )

        # Build Drive service (v3 API)
        _drive_service = build('drive', 'v3', credentials=credentials)
        return _drive_service

    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in ACITRACK_GCP_SA_JSON: {e}")
    except Exception as e:
        raise ValueError(f"Failed to initialize Google Drive service: {e}")


def get_drive_id():
    """
    Get the Shared Drive ID for the folder.
    Caches the result to avoid repeated API calls.

    Returns:
        Drive ID if the folder is in a Shared Drive, None if in My Drive
    """
    global _drive_id

    if _drive_id is not None:
        return _drive_id

    try:
        service = get_drive_service()

        # Get folder metadata including driveId (for Shared Drives)
        folder = service.files().get(
            fileId=DRIVE_FOLDER_ID,
            supportsAllDrives=True,
            fields='id, name, driveId'
        ).execute()

        _drive_id = folder.get('driveId', None)
        return _drive_id

    except HttpError as e:
        # Log the actual error for debugging
        print(f"Error getting drive ID: {e}")
        raise


def find_file_in_folder(filename: str) -> Optional[str]:
    """
    Find a file by exact name in the configured Google Drive folder.
    Supports Shared Drives (formerly Team Drives).

    Args:
        filename: Exact name of the file to find

    Returns:
        File ID if found, None otherwise
    """
    try:
        service = get_drive_service()
        drive_id = get_drive_id()

        # Query for the file in the specific folder (list files inside the folder)
        query = f"name='{filename}' and '{DRIVE_FOLDER_ID}' in parents and trashed=false"

        # Build request parameters for Shared Drive support
        request_params = {
            'q': query,
            'spaces': 'drive',
            'fields': 'files(id, name)',
            'pageSize': 1,
            'supportsAllDrives': True,
            'includeItemsFromAllDrives': True
        }

        # If this is a Shared Drive, specify corpora and driveId
        if drive_id:
            request_params['corpora'] = 'drive'
            request_params['driveId'] = drive_id

        results = service.files().list(**request_params).execute()

        files = results.get('files', [])

        if files:
            return files[0]['id']

        return None

    except HttpError as e:
        # Log the actual error for debugging
        print(f"Error finding file '{filename}': {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Google Drive API error: {e}"
        )


def download_file_content(file_id: str) -> bytes:
    """
    Download file content from Google Drive by file ID.
    Supports Shared Drives (formerly Team Drives).

    Args:
        file_id: Google Drive file ID

    Returns:
        File content as bytes
    """
    try:
        service = get_drive_service()

        # get_media supports Shared Drives automatically with supportsAllDrives
        request = service.files().get_media(
            fileId=file_id,
            supportsAllDrives=True
        )

        file_buffer = BytesIO()
        downloader = MediaIoBaseDownload(file_buffer, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()

        return file_buffer.getvalue()

    except HttpError as e:
        # Log the actual error for debugging
        print(f"Error downloading file '{file_id}': {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to download file: {e}"
        )


def is_cache_valid(filename: str) -> bool:
    """
    Check if the cached content for a file is still valid.

    Args:
        filename: Name of the file to check

    Returns:
        True if cache exists and is not expired, False otherwise
    """
    if filename not in _artifact_cache:
        return False

    cached_at = _artifact_cache[filename].get("cached_at")
    if not cached_at:
        return False

    expiry_time = cached_at + timedelta(minutes=CACHE_TTL_MINUTES)
    return datetime.now() < expiry_time


def get_cached_content(filename: str) -> Optional[bytes]:
    """
    Get cached content if it exists and is valid.

    Args:
        filename: Name of the file to retrieve

    Returns:
        Cached content as bytes, or None if not cached or expired
    """
    if is_cache_valid(filename):
        return _artifact_cache[filename].get("content")
    return None


def set_cache(filename: str, content: bytes, file_id: str):
    """
    Cache file content with timestamp.

    Args:
        filename: Name of the file
        content: File content as bytes
        file_id: Google Drive file ID
    """
    _artifact_cache[filename] = {
        "content": content,
        "cached_at": datetime.now(),
        "file_id": file_id
    }


def get_artifact_with_cache(filename: str, media_type: str = "application/json") -> Response:
    """
    Get artifact from Drive with caching. Falls back to cache if Drive fails.

    Args:
        filename: Name of the file in Google Drive
        media_type: Response content type

    Returns:
        Response with file content

    Raises:
        HTTPException: 404 if file not found, 503 if Drive down and no cache
    """
    # Try to get from cache first
    cached_content = get_cached_content(filename)
    if cached_content:
        print(f"Cache hit for {filename}")
        return Response(content=cached_content, media_type=media_type)

    # Cache miss or expired - fetch from Drive
    try:
        file_id = find_file_in_folder(filename)

        if not file_id:
            # If we have stale cache, return it with a warning header
            if filename in _artifact_cache:
                print(f"File not found in Drive, using stale cache for {filename}")
                stale_content = _artifact_cache[filename].get("content")
                return Response(
                    content=stale_content,
                    media_type=media_type,
                    headers={"X-Cache-Status": "stale"}
                )

            raise HTTPException(
                status_code=404,
                detail=f"File '{filename}' not found in Google Drive folder"
            )

        content = download_file_content(file_id)

        # Update cache
        set_cache(filename, content, file_id)
        print(f"Downloaded and cached {filename}")

        return Response(content=content, media_type=media_type)

    except HTTPException:
        # Re-raise HTTP exceptions (404, etc.)
        raise
    except Exception as e:
        # Drive API error - try to use stale cache
        print(f"Drive API error for {filename}: {e}")

        if filename in _artifact_cache:
            print(f"Using stale cache for {filename} due to Drive error")
            stale_content = _artifact_cache[filename].get("content")
            return Response(
                content=stale_content,
                media_type=media_type,
                headers={"X-Cache-Status": "stale", "X-Error": "Drive API unavailable"}
            )

        # No cache available
        raise HTTPException(
            status_code=503,
            detail=f"Google Drive unavailable and no cached content for '{filename}'"
        )


@app.get("/")
async def root():
    """
    Root endpoint with API information.
    """
    return {
        "name": "AciTrack API",
        "version": "1.1.0",
        "description": "API for accessing latest academic publication reports",
        "endpoints": {
            "/report": "Get latest report (Markdown)",
            "/manifest": "Get latest manifest (JSON)",
            "/new": "Get latest new publications (CSV)",
            "/api/must-reads": "Get must-read publications (JSON)",
            "/api/must-reads/md": "Get must-read publications (Markdown)",
            "/api/summaries": "Get publication summaries (JSON)",
            "/api/db/snapshot": "Download database snapshot (gzip)",
            "/api/artifacts/status": "Get artifacts status and cache info"
        },
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """
    Health check endpoint to verify service and Google Drive connectivity.
    Verifies Shared Drive access and ability to list files.
    """
    try:
        # Verify we can access the Drive service
        service = get_drive_service()

        # Verify the folder exists (with Shared Drive support)
        folder = service.files().get(
            fileId=DRIVE_FOLDER_ID,
            supportsAllDrives=True,
            fields='id, name, driveId'
        ).execute()

        folder_name = folder.get('name')
        drive_id = folder.get('driveId')

        # Verify we can list files in the folder
        drive_id_cached = get_drive_id()

        list_params = {
            'q': f"'{DRIVE_FOLDER_ID}' in parents and trashed=false",
            'spaces': 'drive',
            'fields': 'files(id, name)',
            'pageSize': 5,
            'supportsAllDrives': True,
            'includeItemsFromAllDrives': True
        }

        if drive_id_cached:
            list_params['corpora'] = 'drive'
            list_params['driveId'] = drive_id_cached

        files_result = service.files().list(**list_params).execute()
        files = files_result.get('files', [])
        file_count = len(files)

        return {
            "status": "healthy",
            "drive_connected": True,
            "folder_name": folder_name,
            "folder_id": DRIVE_FOLDER_ID,
            "is_shared_drive": drive_id is not None,
            "drive_id": drive_id,
            "files_found": file_count
        }

    except HttpError as e:
        # Log the actual error
        print(f"Health check failed - HttpError: {e}")
        return {
            "status": "unhealthy",
            "error": f"Google Drive API error: {str(e)}",
            "error_details": str(e)
        }
    except Exception as e:
        # Log the actual error
        print(f"Health check failed - Exception: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }


@app.get("/report")
async def get_report():
    """
    Get the latest report in Markdown format.

    Returns:
        latest_report.md content as text/markdown
    """
    file_id = find_file_in_folder(FILE_NAMES["report"])

    if not file_id:
        raise HTTPException(
            status_code=404,
            detail=f"File '{FILE_NAMES['report']}' not found in Google Drive folder"
        )

    content = download_file_content(file_id)

    return Response(
        content=content,
        media_type="text/markdown"
    )


@app.get("/manifest")
async def get_manifest():
    """
    Get the latest manifest in JSON format.

    Returns:
        latest_manifest.json content as application/json
    """
    file_id = find_file_in_folder(FILE_NAMES["manifest"])

    if not file_id:
        raise HTTPException(
            status_code=404,
            detail=f"File '{FILE_NAMES['manifest']}' not found in Google Drive folder"
        )

    content = download_file_content(file_id)

    # Parse and return as JSON to ensure valid JSON response
    try:
        json_data = json.loads(content.decode('utf-8'))
        return json_data
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid JSON in manifest file: {e}"
        )


@app.get("/new")
async def get_new():
    """
    Get the latest new publications in CSV format.

    Returns:
        latest_new.csv content as text/csv
    """
    file_id = find_file_in_folder(FILE_NAMES["new"])

    if not file_id:
        raise HTTPException(
            status_code=404,
            detail=f"File '{FILE_NAMES['new']}' not found in Google Drive folder"
        )

    content = download_file_content(file_id)

    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "inline; filename=latest_new.csv"}
    )


def upgrade_must_read_schema(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Upgrade a must-read item to include new scoring fields with safe defaults.

    This function ensures backwards compatibility by adding missing fields
    without modifying existing data.

    New fields added:
    - relevancy_score: float or null
    - relevancy_reason: string (empty if not present)
    - credibility_score: float or null
    - credibility_reason: string (empty if not present)
    - scored_at: ISO timestamp string or null
    - scoring_version: string (default "poc_v1" or null)
    - scoring_model: string or null

    Args:
        item: Must-read item dictionary

    Returns:
        Upgraded item with all new fields present
    """
    # Define default values for new fields
    defaults = {
        "relevancy_score": None,
        "relevancy_reason": "",
        "credibility_score": None,
        "credibility_reason": "",
        "scored_at": None,
        "scoring_version": "poc_v1",
        "scoring_model": None
    }

    # Add missing fields with defaults (preserves existing values if present)
    for field, default_value in defaults.items():
        if field not in item:
            item[field] = default_value

    return item


@app.get("/api/must-reads")
async def get_must_reads():
    """
    Get the latest must-read publications in JSON format.
    Uses caching with 10-minute TTL. Falls back to stale cache if Drive is unavailable.

    Returns:
        latest_must_reads.json content as application/json with upgraded schema
    """
    response = get_artifact_with_cache(FILE_NAMES["must_reads_json"], "application/json")

    # Parse JSON to return proper JSON response
    try:
        json_data = json.loads(response.body.decode('utf-8'))

        # Schema upgrade: Add new scoring fields to each must-read item
        # This ensures backwards compatibility with older JSON files in Drive
        if isinstance(json_data, dict) and "must_reads" in json_data:
            # Handle structure: {"must_reads": [...]}
            json_data["must_reads"] = [
                upgrade_must_read_schema(item) for item in json_data["must_reads"]
            ]
        elif isinstance(json_data, list):
            # Handle structure: [...]
            json_data = [upgrade_must_read_schema(item) for item in json_data]

        return Response(
            content=json.dumps(json_data, indent=2),
            media_type="application/json",
            headers=dict(response.headers)
        )
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid JSON in must-reads file: {e}"
        )


@app.get("/api/must-reads/md")
async def get_must_reads_md():
    """
    Get the latest must-read publications in Markdown format.
    Uses caching with 10-minute TTL. Falls back to stale cache if Drive is unavailable.

    Returns:
        latest_must_reads.md content as text/markdown
    """
    return get_artifact_with_cache(FILE_NAMES["must_reads_md"], "text/markdown")


@app.get("/api/summaries")
async def get_summaries():
    """
    Get the latest publication summaries in JSON format.
    Uses caching with 10-minute TTL. Falls back to stale cache if Drive is unavailable.

    Returns:
        latest_summaries.json content as application/json
    """
    response = get_artifact_with_cache(FILE_NAMES["summaries"], "application/json")

    # Parse JSON to return proper JSON response
    try:
        json_data = json.loads(response.body.decode('utf-8'))
        return Response(
            content=json.dumps(json_data),
            media_type="application/json",
            headers=dict(response.headers)
        )
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid JSON in summaries file: {e}"
        )


@app.get("/api/db/snapshot")
async def get_db_snapshot():
    """
    Download the latest database snapshot as a gzipped SQLite file.
    Uses caching with 10-minute TTL. Falls back to stale cache if Drive is unavailable.

    Returns:
        latest_db.sqlite.gz as application/gzip (downloadable)
    """
    response = get_artifact_with_cache(FILE_NAMES["db_snapshot"], "application/gzip")

    # Add Content-Disposition header for download
    headers = dict(response.headers)
    headers["Content-Disposition"] = "attachment; filename=latest_db.sqlite.gz"

    return Response(
        content=response.body,
        media_type="application/gzip",
        headers=headers
    )


@app.get("/api/artifacts/status")
async def get_artifacts_status():
    """
    Get status information for all artifacts including cache status.

    Returns:
        JSON with artifact availability, file IDs, and cache timestamps
    """
    artifacts_status = {}

    # Check each artifact
    for key, filename in FILE_NAMES.items():
        try:
            # Try to find the file in Drive
            file_id = find_file_in_folder(filename)

            # Check cache status
            cache_info = None
            if filename in _artifact_cache:
                cached_at = _artifact_cache[filename].get("cached_at")
                cache_info = {
                    "cached": True,
                    "cached_at": cached_at.isoformat() if cached_at else None,
                    "is_valid": is_cache_valid(filename),
                    "ttl_minutes": CACHE_TTL_MINUTES
                }
            else:
                cache_info = {"cached": False}

            artifacts_status[key] = {
                "filename": filename,
                "available": file_id is not None,
                "file_id": file_id,
                "cache": cache_info
            }

        except Exception as e:
            artifacts_status[key] = {
                "filename": filename,
                "available": False,
                "error": str(e),
                "cache": {
                    "cached": filename in _artifact_cache,
                    "cached_at": _artifact_cache[filename].get("cached_at").isoformat()
                        if filename in _artifact_cache and _artifact_cache[filename].get("cached_at")
                        else None
                }
            }

    return {
        "status": "ok",
        "cache_ttl_minutes": CACHE_TTL_MINUTES,
        "artifacts": artifacts_status
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
