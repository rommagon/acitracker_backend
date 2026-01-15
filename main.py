"""
AciTrack Backend API
A FastAPI service that exposes academic publication data from Google Drive
for consumption by Custom GPT Actions.
"""

import os
import json
import logging
import tempfile
from typing import Optional, Dict, Any, List
from io import BytesIO
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Response, Query
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

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

# Legacy file names to search for in Google Drive (deprecated)
FILE_NAMES = {
    "report": "latest_report.md",
    "manifest": "latest_manifest.json",
    "new": "latest_new.csv",
    "must_reads_json": "latest_must_reads.json",
    "must_reads_md": "latest_must_reads.md",
    "summaries": "latest_summaries.json",
    "db_snapshot": "latest_db.sqlite.gz"
}

# New manifest-based folder structure
MANIFESTS_FOLDER = "manifests"
OUTPUTS_FOLDER = "outputs"

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


def find_folder_in_parent(folder_name: str, parent_id: str) -> Optional[str]:
    """
    Find a folder by exact name within a parent folder.

    Args:
        folder_name: Name of the folder to find
        parent_id: Parent folder ID to search in

    Returns:
        Folder ID if found, None otherwise
    """
    try:
        service = get_drive_service()
        drive_id = get_drive_id()

        query = f"name='{folder_name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"

        request_params = {
            'q': query,
            'spaces': 'drive',
            'fields': 'files(id, name)',
            'pageSize': 1,
            'supportsAllDrives': True,
            'includeItemsFromAllDrives': True
        }

        if drive_id:
            request_params['corpora'] = 'drive'
            request_params['driveId'] = drive_id

        results = service.files().list(**request_params).execute()
        files = results.get('files', [])

        if files:
            return files[0]['id']

        return None

    except HttpError as e:
        print(f"Error finding folder '{folder_name}': {e}")
        return None


def get_latest_run(mode: str = "daily") -> Dict[str, Any]:
    """
    Get the latest run information for a given mode (daily or weekly).

    This function:
    1. Reads Manifests/<Mode>/latest.json to get the latest run_id and Drive references
    2. Loads the full manifest using drive_manifest_path or drive_manifest_file_id if available
    3. Returns manifest with Drive file IDs and paths for direct access

    Args:
        mode: Either "daily" or "weekly" (default: "daily")

    Returns:
        Dictionary with:
        - run_id: The latest run ID
        - manifest: The full manifest data (includes drive_file_ids, drive_output_paths)
        - latest_pointer: The latest.json data for reference
        - mode: The run mode

    Raises:
        HTTPException: 503 if latest.json or manifest is missing/unreadable
    """
    if mode not in ["daily", "weekly"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{mode}'. Must be 'daily' or 'weekly'"
        )

    try:
        service = get_drive_service()
        mode_capitalized = mode.capitalize()  # "daily" -> "Daily", "weekly" -> "Weekly"

        # Step 1: Find Manifests folder
        manifests_folder_id = find_folder_in_parent("Manifests", DRIVE_FOLDER_ID)
        if not manifests_folder_id:
            raise HTTPException(
                status_code=503,
                detail=f"Manifests folder not found in Drive"
            )

        # Step 2: Find mode folder (Daily or Weekly)
        mode_folder_id = find_folder_in_parent(mode_capitalized, manifests_folder_id)
        if not mode_folder_id:
            raise HTTPException(
                status_code=503,
                detail=f"No Manifests/{mode_capitalized} folder for mode={mode}"
            )

        # Step 3: Read latest.json
        latest_file_id = find_file_in_folder_by_id("latest.json", mode_folder_id)
        if not latest_file_id:
            raise HTTPException(
                status_code=503,
                detail=f"No latest pointer for mode={mode}"
            )

        latest_content = download_file_content(latest_file_id)
        latest_data = json.loads(latest_content.decode('utf-8'))

        run_id = latest_data.get("run_id")
        if not run_id:
            raise HTTPException(
                status_code=503,
                detail=f"Invalid latest.json for mode={mode}: missing run_id"
            )

        # Step 4: Load manifest - prefer drive_manifest_path/file_id if available
        manifest_content = None

        # Try drive_manifest_file_id first (fastest - direct file access)
        if "drive_manifest_file_id" in latest_data:
            try:
                manifest_file_id = latest_data["drive_manifest_file_id"]
                manifest_content = download_file_content(manifest_file_id)
                logger.info(f"Loaded manifest via drive_manifest_file_id: {manifest_file_id}")
            except Exception as e:
                logger.warning(f"Failed to load manifest by file ID, trying path: {e}")

        # Fallback to drive_manifest_path (navigate folders)
        if not manifest_content and "drive_manifest_path" in latest_data:
            try:
                drive_manifest_path = latest_data["drive_manifest_path"]
                manifest_content = get_output_file_content(drive_manifest_path)
                logger.info(f"Loaded manifest via drive_manifest_path: {drive_manifest_path}")
            except Exception as e:
                logger.warning(f"Failed to load manifest by Drive path, trying local path: {e}")

        # Final fallback to local manifest path (manifests/<mode>/<run_id>.json)
        if not manifest_content:
            manifest_filename = f"{run_id}.json"
            manifest_file_id = find_file_in_folder_by_id(manifest_filename, mode_folder_id)
            if not manifest_file_id:
                raise HTTPException(
                    status_code=503,
                    detail=f"Manifest file not found: Manifests/{mode_capitalized}/{manifest_filename}"
                )
            manifest_content = download_file_content(manifest_file_id)
            logger.info(f"Loaded manifest via fallback path: {manifest_filename}")

        manifest_data = json.loads(manifest_content.decode('utf-8'))

        return {
            "run_id": run_id,
            "manifest": manifest_data,
            "latest_pointer": latest_data,
            "mode": mode
        }

    except HTTPException:
        raise
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Invalid JSON in manifest for mode={mode}: {e}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to resolve latest run for mode={mode}: {e}"
        )


def find_file_in_folder_by_id(filename: str, folder_id: str) -> Optional[str]:
    """
    Find a file by exact name in a specific folder ID.

    Args:
        filename: Exact name of the file to find
        folder_id: Folder ID to search in

    Returns:
        File ID if found, None otherwise
    """
    try:
        service = get_drive_service()
        drive_id = get_drive_id()

        query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"

        request_params = {
            'q': query,
            'spaces': 'drive',
            'fields': 'files(id, name)',
            'pageSize': 1,
            'supportsAllDrives': True,
            'includeItemsFromAllDrives': True
        }

        if drive_id:
            request_params['corpora'] = 'drive'
            request_params['driveId'] = drive_id

        results = service.files().list(**request_params).execute()
        files = results.get('files', [])

        if files:
            return files[0]['id']

        return None

    except HttpError as e:
        print(f"Error finding file '{filename}' in folder {folder_id}: {e}")
        return None


def resolve_and_get_output_file(
    file_key: str,
    latest_run: Dict[str, Any],
    debug: bool = False
) -> tuple[bytes, Optional[Dict[str, str]]]:
    """
    Resolve and retrieve output file content with fallback priority:
    1. drive_file_ids[file_key] or drive_file_ids[file_key_alt] (fastest - direct file access)
    2. drive_output_paths[file_key] or drive_output_paths[file_key_alt] (navigate Drive folders)
    3. Smart folder traversal: navigate to run folder and list files by exact name
    4. output_paths[file_key] (legacy local path fallback)

    Args:
        file_key: Key name (e.g., "must_reads", "report", "new", "summaries")
        latest_run: Dictionary from get_latest_run() containing manifest and latest_pointer
        debug: If True, return resolution metadata

    Returns:
        Tuple of (file_content, debug_info)
        debug_info contains: {"resolution_method": str, "resolved_path": str}

    Raises:
        HTTPException: 503 if file cannot be found via any method
    """
    manifest = latest_run["manifest"]
    latest_pointer = latest_run.get("latest_pointer", {})
    mode = latest_run["mode"]
    run_id = latest_run["run_id"]

    debug_info = None
    content = None

    # Define alternative key names for manifest compatibility
    # Manifest may use "must_reads" or "must_reads_json", etc.
    key_alternatives = {
        "must_reads": ["must_reads", "must_reads_json"],
        "report": ["report", "report_md"],
        "new": ["new", "new_csv"],
        "summaries": ["summaries", "summaries_json"]
    }

    # Get list of keys to try (both base key and alternative)
    keys_to_try = key_alternatives.get(file_key, [file_key])

    logger.info(f"[{mode}/{run_id}] Resolving '{file_key}' - will try keys: {keys_to_try}")

    # Priority 1: Try drive_file_ids (fastest - direct file ID access)
    drive_file_ids = latest_pointer.get("drive_file_ids") or manifest.get("drive_file_ids")
    if drive_file_ids:
        for key_variant in keys_to_try:
            if key_variant in drive_file_ids:
                try:
                    file_id = drive_file_ids[key_variant]
                    logger.info(f"[{mode}/{run_id}] Trying drive_file_id for key '{key_variant}': {file_id}")
                    content = download_file_content(file_id)
                    debug_info = {
                        "resolution_method": "drive_file_id",
                        "resolved_path": file_id,
                        "file_key": file_key,
                        "manifest_key": key_variant
                    }
                    logger.info(f"[{mode}/{run_id}] ✓ Resolved '{file_key}' via drive_file_id['{key_variant}']: {file_id}")
                    break
                except Exception as e:
                    logger.warning(f"[{mode}/{run_id}] Failed to load '{key_variant}' by file ID {file_id}: {e}")

    # Priority 2: Try drive_output_paths (navigate Drive folders)
    if not content:
        drive_output_paths = latest_pointer.get("drive_output_paths") or manifest.get("drive_output_paths")
        if drive_output_paths:
            for key_variant in keys_to_try:
                if key_variant in drive_output_paths:
                    try:
                        drive_path = drive_output_paths[key_variant]
                        logger.info(f"[{mode}/{run_id}] Trying drive_output_path for key '{key_variant}': {drive_path}")
                        content = get_output_file_content(drive_path)
                        debug_info = {
                            "resolution_method": "drive_output_path",
                            "resolved_path": drive_path,
                            "file_key": file_key,
                            "manifest_key": key_variant
                        }
                        logger.info(f"[{mode}/{run_id}] ✓ Resolved '{file_key}' via drive_output_path['{key_variant}']: {drive_path}")
                        break
                    except Exception as e:
                        logger.warning(f"[{mode}/{run_id}] Failed to load '{key_variant}' by Drive path {drive_path}: {e}")

    # Priority 3: Smart folder traversal fallback
    # Navigate to the run folder and list files by exact filename
    if not content:
        logger.info(f"[{mode}/{run_id}] Attempting smart folder traversal fallback")
        try:
            # Define expected filenames
            filename_map = {
                "must_reads": "must_reads.json",
                "report": "report.md",
                "new": "new.csv",
                "summaries": "summaries.json"
            }
            expected_filename = filename_map.get(file_key)

            if expected_filename:
                # Navigate: root -> "Daily" or "Weekly" -> run_id -> filename
                mode_capitalized = mode.capitalize()  # "daily" -> "Daily", "weekly" -> "Weekly"

                logger.info(f"[{mode}/{run_id}] Looking for file in: {mode_capitalized}/{run_id}/{expected_filename}")

                # Step 1: Find mode folder (Daily or Weekly)
                mode_folder_id = find_folder_in_parent(mode_capitalized, DRIVE_FOLDER_ID)
                if not mode_folder_id:
                    logger.warning(f"[{mode}/{run_id}] Mode folder '{mode_capitalized}' not found in Drive root")
                else:
                    # Step 2: Find run folder
                    run_folder_id = find_folder_in_parent(run_id, mode_folder_id)
                    if not run_folder_id:
                        logger.warning(f"[{mode}/{run_id}] Run folder '{run_id}' not found in {mode_capitalized}/")
                    else:
                        # Step 3: Find file in run folder
                        file_id = find_file_in_folder_by_id(expected_filename, run_folder_id)
                        if file_id:
                            content = download_file_content(file_id)
                            debug_info = {
                                "resolution_method": "smart_folder_traversal",
                                "resolved_path": f"{mode_capitalized}/{run_id}/{expected_filename}",
                                "file_key": file_key,
                                "file_id": file_id
                            }
                            logger.info(f"[{mode}/{run_id}] ✓ Resolved '{file_key}' via folder traversal: {expected_filename}")
                        else:
                            logger.warning(f"[{mode}/{run_id}] File '{expected_filename}' not found in {mode_capitalized}/{run_id}/")
        except Exception as e:
            logger.warning(f"[{mode}/{run_id}] Smart folder traversal failed: {e}")

    # Priority 4: Fallback to local output_paths (legacy)
    if not content:
        logger.info(f"[{mode}/{run_id}] Attempting legacy output_paths fallback")
        output_paths = manifest.get("output_paths") or manifest.get("local_output_paths", {})

        # Map file_key to output_paths key
        key_mapping = {
            "must_reads": "must_reads_json",
            "report": "report_md",
            "new": "new_csv",
            "summaries": "summaries_json"
        }

        output_key = key_mapping.get(file_key, file_key)
        if output_key in output_paths:
            try:
                local_path = output_paths[output_key]
                # Convert local path to Drive path (e.g., data/outputs/daily/run_id/file.json -> outputs/daily/run_id/file.json)
                # Strip "data/" prefix if present
                drive_path = local_path.replace("data/", "")
                logger.info(f"[{mode}/{run_id}] Trying legacy path: {drive_path}")
                content = get_output_file_content(drive_path)
                debug_info = {
                    "resolution_method": "local_fallback",
                    "resolved_path": drive_path,
                    "file_key": file_key
                }
                logger.info(f"[{mode}/{run_id}] ✓ Resolved '{file_key}' via local fallback: {drive_path}")
            except Exception as e:
                logger.error(f"[{mode}/{run_id}] Failed to load '{file_key}' via local fallback: {e}")

    if not content:
        error_detail = f"Output file '{file_key}' not found via any resolution method for mode={mode}, run_id={run_id}. Tried: drive_file_ids={list(drive_file_ids.keys()) if drive_file_ids else 'N/A'}, drive_output_paths={list(drive_output_paths.keys()) if drive_output_paths else 'N/A'}, folder_traversal, legacy_paths"
        logger.error(f"[{mode}/{run_id}] {error_detail}")
        raise HTTPException(
            status_code=503,
            detail=error_detail
        )

    return content, debug_info if debug else None


def get_output_file_content(output_path: str) -> bytes:
    """
    Get content of an output file by path (e.g., outputs/daily/run_123/must_reads.json).

    This function navigates the folder hierarchy in Drive to find and download the file.

    Args:
        output_path: Path like "outputs/daily/run_123/must_reads.json"

    Returns:
        File content as bytes

    Raises:
        HTTPException: 503 if file not found
    """
    try:
        service = get_drive_service()

        # Split path into components
        parts = output_path.split('/')
        if len(parts) < 2:
            raise HTTPException(
                status_code=503,
                detail=f"Invalid output path: {output_path}"
            )

        # Navigate folder hierarchy
        current_folder_id = DRIVE_FOLDER_ID

        # Navigate through all folders in the path
        for i, part in enumerate(parts[:-1]):  # All parts except filename
            folder_id = find_folder_in_parent(part, current_folder_id)
            if not folder_id:
                raise HTTPException(
                    status_code=503,
                    detail=f"Folder not found in path: {'/'.join(parts[:i+1])}"
                )
            current_folder_id = folder_id

        # Find the file in the final folder
        filename = parts[-1]
        file_id = find_file_in_folder_by_id(filename, current_folder_id)
        if not file_id:
            raise HTTPException(
                status_code=503,
                detail=f"Output file not found: {output_path}"
            )

        return download_file_content(file_id)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to retrieve output file {output_path}: {e}"
        )


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
        "version": "2.0.0",
        "description": "API for accessing latest academic publication reports with daily/weekly mode support",
        "mode_support": {
            "description": "Most endpoints support ?mode=daily|weekly parameter",
            "default": "daily",
            "valid_values": ["daily", "weekly"]
        },
        "endpoints": {
            "/report": "Get latest report (Markdown) - supports ?mode=daily|weekly",
            "/manifest": "Get latest manifest (JSON) - supports ?mode=daily|weekly",
            "/new": "Get latest new publications (CSV) - supports ?mode=daily|weekly",
            "/api/must-reads": "Get must-read publications (JSON) - supports ?mode=daily|weekly",
            "/api/must-reads/md": "Get must-read publications (Markdown)",
            "/api/summaries": "Get publication summaries (JSON) - supports ?mode=daily|weekly",
            "/api/db/snapshot": "Download database snapshot (gzip)",
            "/api/artifacts/status": "Get artifacts status and cache info"
        },
        "examples": {
            "daily_must_reads": "/api/must-reads?mode=daily",
            "weekly_must_reads": "/api/must-reads?mode=weekly",
            "daily_report": "/report?mode=daily",
            "weekly_manifest": "/manifest?mode=weekly"
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
async def get_report(
    mode: str = Query(default="daily", description="Run mode: daily or weekly"),
    debug: bool = Query(default=False, description="Include resolution debug info in headers")
):
    """
    Get the latest report in Markdown format for a specific mode.

    This endpoint loads the report.md for the latest run using:
    1. drive_file_ids["report"] (fastest)
    2. drive_output_paths["report"] (Drive folder navigation)
    3. output_paths fallback (legacy)

    Query Parameters:
    - mode: Either "daily" or "weekly" (default: "daily")
    - debug: If true, add X-Resolution-Method header

    Returns:
        report.md content as text/markdown

    Examples:
    - /report?mode=daily
    - /report?mode=weekly&debug=true
    """
    latest_run = get_latest_run(mode)
    content, debug_info = resolve_and_get_output_file("report", latest_run, debug=debug)

    headers = {}
    if debug and debug_info:
        headers["X-Resolution-Method"] = debug_info["resolution_method"]
        headers["X-Resolved-Path"] = debug_info["resolved_path"]

    return Response(
        content=content,
        media_type="text/markdown",
        headers=headers
    )


@app.get("/manifest")
async def get_manifest(
    mode: str = Query(default="daily", description="Run mode: daily or weekly"),
    include_warnings: bool = Query(default=True, description="Include validation warnings")
):
    """
    Get the latest manifest in JSON format for a specific mode.

    This endpoint loads the manifest for the latest run of the specified mode:
    1. Reads manifests/<mode>/latest.json to get the latest run_id
    2. Loads and returns manifests/<mode>/<run_id>.json
    3. Optionally adds validation warnings if Drive fields are missing

    Query Parameters:
    - mode: Either "daily" or "weekly" (default: "daily")
    - include_warnings: Add validation warnings for missing Drive fields (default: true)

    Returns:
        Manifest JSON for the latest run of the specified mode

    Examples:
    - /manifest?mode=daily
    - /manifest?mode=weekly
    - /manifest?mode=daily&include_warnings=false
    """
    latest_run = get_latest_run(mode)
    manifest = latest_run["manifest"]

    if include_warnings:
        warnings = []

        # Check for Drive file IDs
        drive_file_ids = manifest.get("drive_file_ids")
        if not drive_file_ids:
            warnings.append("Missing 'drive_file_ids' - backend will use slower folder traversal")

        # Check for Drive output paths
        drive_output_paths = manifest.get("drive_output_paths")
        if not drive_output_paths:
            warnings.append("Missing 'drive_output_paths' - backend will use slower folder traversal")

        # Check for local paths (should be present for backward compatibility)
        output_paths = manifest.get("output_paths") or manifest.get("local_output_paths")
        if not output_paths:
            warnings.append("Missing 'output_paths' and 'local_output_paths' - no legacy fallback available")

        # Add warnings to response if any exist
        if warnings:
            result = {
                "_warnings": warnings,
                "_recommendation": "Update pipeline to include drive_file_ids and drive_output_paths in manifest",
                **manifest
            }
            return result

    return manifest


@app.get("/new")
async def get_new(
    mode: str = Query(default="daily", description="Run mode: daily or weekly"),
    debug: bool = Query(default=False, description="Include resolution debug info in headers")
):
    """
    Get the latest new publications in CSV format for a specific mode.

    This endpoint loads the new.csv for the latest run using:
    1. drive_file_ids["new"] (fastest)
    2. drive_output_paths["new"] (Drive folder navigation)
    3. output_paths fallback (legacy)

    Query Parameters:
    - mode: Either "daily" or "weekly" (default: "daily")
    - debug: If true, add X-Resolution-Method header

    Returns:
        new.csv content as text/csv

    Examples:
    - /new?mode=daily
    - /new?mode=weekly&debug=true
    """
    latest_run = get_latest_run(mode)
    content, debug_info = resolve_and_get_output_file("new", latest_run, debug=debug)

    headers = {"Content-Disposition": f"inline; filename=new_{mode}.csv"}
    if debug and debug_info:
        headers["X-Resolution-Method"] = debug_info["resolution_method"]
        headers["X-Resolved-Path"] = debug_info["resolved_path"]

    return Response(
        content=content,
        media_type="text/csv",
        headers=headers
    )


def normalize_must_read_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize and upgrade a must-read item to include all required fields with safe defaults.

    This function ensures backwards compatibility and UI-readiness by:
    1. Adding missing relevancy and credibility fields
    2. Normalizing text fields (single-line strings)
    3. Clamping scores to 0-100 range
    4. Providing safe defaults for all fields

    Args:
        item: Must-read item dictionary

    Returns:
        Normalized item with all required fields present
    """
    # Define default values for all LLM-generated scoring fields
    defaults = {
        # Relevancy fields
        "relevancy_score": None,
        "relevancy_reason": "",
        "signals": {},
        # Credibility fields
        "credibility_score": None,
        "credibility_reason": "",
        "credibility_confidence": None,
        "credibility_signals": {},
        # Metadata
        "scored_at": None,
        "scoring_version": "poc_v1",
        "scoring_model": None
    }

    # Add missing fields with defaults (preserves existing values if present)
    for field, default_value in defaults.items():
        if field not in item:
            item[field] = default_value

    # Normalize relevancy_reason: convert to single-line string
    if item.get("relevancy_reason"):
        item["relevancy_reason"] = " ".join(str(item["relevancy_reason"]).split())

    # Normalize credibility_reason: convert to single-line string
    if item.get("credibility_reason"):
        item["credibility_reason"] = " ".join(str(item["credibility_reason"]).split())

    # Clamp relevancy_score to 0-100 if present
    if item.get("relevancy_score") is not None:
        try:
            score = int(item["relevancy_score"])
            item["relevancy_score"] = max(0, min(100, score))
        except (ValueError, TypeError):
            item["relevancy_score"] = None

    # Clamp credibility_score to 0-100 if present
    if item.get("credibility_score") is not None:
        try:
            score = int(item["credibility_score"])
            item["credibility_score"] = max(0, min(100, score))
        except (ValueError, TypeError):
            item["credibility_score"] = None

    # Ensure signals and credibility_signals are dictionaries
    if not isinstance(item.get("signals"), dict):
        item["signals"] = {}
    if not isinstance(item.get("credibility_signals"), dict):
        item["credibility_signals"] = {}

    return item


def filter_and_sort_must_reads(
    items: List[Dict[str, Any]],
    min_relevance: int = 30,
    include_zero: bool = False,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Filter and sort must-read items for UI consumption.

    Filtering logic:
    - If include_zero=False: filter out items where relevancy_score is None or < min_relevance
    - If include_zero=True: no filtering by relevancy_score

    Sorting logic:
    - Primary: relevancy_score DESC (treat None as -1)
    - Secondary: published_date DESC (parse ISO timestamps safely)

    Args:
        items: List of must-read items
        min_relevance: Minimum relevancy score threshold (default 30)
        include_zero: If True, include items with null/low relevancy scores (default False)
        limit: Maximum number of items to return (default 5)

    Returns:
        Filtered and sorted list of items (up to limit)
    """
    # Step 1: Filter by relevancy score
    filtered = items
    if not include_zero:
        filtered = [
            item for item in items
            if item.get("relevancy_score") is not None and item.get("relevancy_score") >= min_relevance
        ]

    # Step 2: Sort by relevancy_score DESC, then published_date DESC
    def sort_key(item):
        # Primary sort: relevancy_score (treat None as -1)
        relevancy = item.get("relevancy_score")
        relevancy_val = relevancy if relevancy is not None else -1

        # Secondary sort: published_date (parse ISO timestamp safely)
        published_date = item.get("published_date", "")
        try:
            # Try to parse ISO timestamp
            date_obj = datetime.fromisoformat(published_date.replace("Z", "+00:00"))
            date_val = date_obj.timestamp()
        except (ValueError, AttributeError):
            # If parsing fails, use epoch (oldest possible date)
            date_val = 0

        # Return tuple for sorting: (relevancy DESC, date DESC)
        return (-relevancy_val, -date_val)

    sorted_items = sorted(filtered, key=sort_key)

    # Step 3: Limit results
    return sorted_items[:limit]


@app.get("/api/must-reads")
async def get_must_reads(
    mode: str = Query(default="daily", description="Run mode: daily or weekly"),
    limit: int = Query(default=5, ge=1, le=100, description="Maximum number of items to return"),
    min_relevance: int = Query(default=30, ge=0, le=100, description="Minimum relevancy score threshold"),
    include_zero: bool = Query(default=False, description="Include items with null/low relevancy scores"),
    debug: bool = Query(default=False, description="Include debug information in response")
):
    """
    Get the latest must-read publications in JSON format with filtering and sorting.

    This endpoint returns a UI-ready payload with:
    - Normalized relevancy and credibility fields
    - Filtering by relevancy score (configurable)
    - Sorting by relevancy_score DESC, then published_date DESC
    - Limited to top N items (default 5)
    - Uses drive_file_ids for fastest access

    Query Parameters:
    - mode: Either "daily" or "weekly" (default: "daily")
    - limit: Maximum number of items to return (default 5, max 100)
    - min_relevance: Minimum relevancy score threshold (default 30, 0-100)
    - include_zero: If true, ignore min_relevance filter (default false)
    - debug: If true, include debug metadata (resolution method, run_id, etc)

    Returns:
        Filtered, sorted, and normalized must-reads with all required fields

    Examples:
    - /api/must-reads?mode=daily&limit=5&min_relevance=40
    - /api/must-reads?mode=weekly&include_zero=true&limit=10
    - /api/must-reads?mode=daily&debug=true
    """
    latest_run = get_latest_run(mode)
    content, resolution_info = resolve_and_get_output_file("must_reads", latest_run, debug=debug)

    # Parse JSON to return proper JSON response
    try:
        json_data = json.loads(content.decode('utf-8'))

        # Extract items from the response structure
        items = []
        metadata = {}

        if isinstance(json_data, dict) and "must_reads" in json_data:
            # Handle structure: {"must_reads": [...], "generated_at": ..., etc.}
            items = json_data.get("must_reads", [])
            # Preserve top-level metadata
            metadata = {k: v for k, v in json_data.items() if k != "must_reads"}
        elif isinstance(json_data, list):
            # Handle structure: [...]
            items = json_data
        else:
            raise HTTPException(
                status_code=500,
                detail="Unexpected JSON structure in must-reads file"
            )

        # Step 1: Normalize all items (add missing fields, clamp scores, etc.)
        normalized_items = [normalize_must_read_item(item) for item in items]

        # Step 2: Filter and sort
        filtered_sorted = filter_and_sort_must_reads(
            normalized_items,
            min_relevance=min_relevance,
            include_zero=include_zero,
            limit=limit
        )

        # Step 3: Build response payload
        result = {
            "must_reads": filtered_sorted,
            **metadata  # Include original metadata (generated_at, window_days, etc.)
        }

        # Add debug info if requested
        if debug:
            result["_debug"] = {
                "total_items_before_filter": len(normalized_items),
                "total_items_after_filter": len(filtered_sorted),
                "query_params": {
                    "limit": limit,
                    "min_relevance": min_relevance,
                    "include_zero": include_zero,
                    "mode": mode
                },
                "run_id": latest_run["run_id"],
                "resolution": resolution_info  # Show which resolution method was used
            }

        return Response(
            content=json.dumps(result, indent=2),
            media_type="application/json"
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
async def get_summaries(
    mode: str = Query(default="daily", description="Run mode: daily or weekly"),
    debug: bool = Query(default=False, description="Include resolution debug info in headers")
):
    """
    Get the latest publication summaries in JSON format for a specific mode.

    This endpoint loads the summaries.json for the latest run using:
    1. drive_file_ids["summaries"] (fastest)
    2. drive_output_paths["summaries"] (Drive folder navigation)
    3. output_paths fallback (legacy)

    Query Parameters:
    - mode: Either "daily" or "weekly" (default: "daily")
    - debug: If true, add X-Resolution-Method header

    Returns:
        summaries.json content as application/json

    Examples:
    - /api/summaries?mode=daily
    - /api/summaries?mode=weekly&debug=true
    """
    latest_run = get_latest_run(mode)
    content, debug_info = resolve_and_get_output_file("summaries", latest_run, debug=debug)

    # Parse JSON to return proper JSON response
    try:
        json_data = json.loads(content.decode('utf-8'))

        headers = {}
        if debug and debug_info:
            headers["X-Resolution-Method"] = debug_info["resolution_method"]
            headers["X-Resolved-Path"] = debug_info["resolved_path"]

        return Response(
            content=json.dumps(json_data),
            media_type="application/json",
            headers=headers
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
