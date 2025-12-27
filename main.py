"""
AciTrack Backend API
A FastAPI service that exposes academic publication data from Google Drive
for consumption by Custom GPT Actions.
"""

import os
import json
import tempfile
from typing import Optional
from io import BytesIO

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
    "new": "latest_new.csv"
}

# Global Drive service instance and drive ID cache
_drive_service = None
_drive_id = None


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


@app.get("/")
async def root():
    """
    Root endpoint with API information.
    """
    return {
        "name": "AciTrack API",
        "version": "1.0.0",
        "description": "API for accessing latest academic publication reports",
        "endpoints": {
            "/report": "Get latest report (Markdown)",
            "/manifest": "Get latest manifest (JSON)",
            "/new": "Get latest new publications (CSV)"
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


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
