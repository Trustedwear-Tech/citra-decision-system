# Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
# Author: Rohit Kumar Chandan
# SPDX-License-Identifier: BUSL-1.1
#
# Licensed under the Business Source License 1.1. Non-production use is granted;
# production use requires a commercial licence until the Change Date, after
# which this file converts to Apache-2.0. See LICENSE at the repository root.

# bucket.py
"""
Vendor-neutral object-storage client (S3 / MinIO).
Uses boto3 under the hood — works with any S3-compatible endpoint.
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Body
from fastapi.responses import JSONResponse, StreamingResponse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import os
import logging
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from botocore.client import Config

# Import auth middleware for secure user_id extraction
from citra_auth import get_secure_user_id

# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_DOWNLOAD_CORS_ORIGIN = os.getenv("DOWNLOAD_CORS_ALLOW_ORIGIN", "http://localhost:8081")

_client_cache: Optional[boto3.client] = None


def get_environment_prefix() -> str:
    """Get environment prefix for bucket folder structure."""
    env = os.getenv("ENVIRONMENT", "dev").lower()
    if env not in ["dev", "test", "prod"]:
        logger.warning(f"Unknown environment '{env}', defaulting to 'dev'")
        env = "dev"
    return env


def _get_client() -> boto3.client:
    """Return cached S3-compatible client instance."""
    global _client_cache

    if _client_cache is None:
        try:
            access_key = os.getenv('BUCKET_ACCESS_KEY')
            secret_key = os.getenv('BUCKET_SECRET_KEY')
            region = os.getenv('BUCKET_REGION', 'us-east-1')
            endpoint_url = os.getenv('BUCKET_ENDPOINT_URL', '').strip() or None
            
            if not access_key or not secret_key:
                raise ValueError("Bucket credentials not found in environment variables")
            
            # Virtual-hosted style ("<bucket>.<host>/<key>") is what AWS S3 wants,
            # but it breaks against every S3-compatible service: with
            # BUCKET_ENDPOINT_URL pointed at MinIO, boto3 dials
            # "<bucket>.<host>:<port>", which does not resolve. MinIO serves path
            # style ("<host>/<bucket>/<key>"), so a custom endpoint implies path.
            addressing_style = 'path' if endpoint_url else 'virtual'

            client_kwargs = dict(
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=Config(signature_version='s3v4',
                              s3={'addressing_style': addressing_style}),
            )
            if endpoint_url:
                client_kwargs['endpoint_url'] = endpoint_url
            elif region and region != 'us-east-1':
                # Force regional endpoint so SigV4-presigned URLs match the signed region
                client_kwargs['endpoint_url'] = f"https://s3.{region}.amazonaws.com"
            
            _client_cache = boto3.client('s3', **client_kwargs)
            
            if endpoint_url:
                logger.info(f"✅ Bucket client initialized (endpoint: {endpoint_url})")
            else:
                logger.info(f"✅ Bucket client initialized (AWS region: {region})")
        except Exception as exc:
            logger.error(f"Failed to create bucket client: {exc}")
            raise HTTPException(status_code=500, detail="Failed to initialize storage client")

    return _client_cache


# Public alias used by workflow engine nodes
get_client = _get_client

# ── Backward-compat aliases (will be removed in a future release) ──
_get_s3_client = _get_client
get_s3_client = _get_client


def get_config():
    """Get bucket configuration from environment variables."""
    bucket_name = os.getenv("BUCKET_NAME")
    access_key = os.getenv("BUCKET_ACCESS_KEY")
    secret_key = os.getenv("BUCKET_SECRET_KEY")
    region = os.getenv("BUCKET_REGION", "us-east-1")
    
    if not bucket_name or not access_key or not secret_key:
        raise HTTPException(
            status_code=500,
            detail="Bucket configuration not found. Please set BUCKET_NAME, BUCKET_ACCESS_KEY, and BUCKET_SECRET_KEY environment variables."
        )
    
    return bucket_name, region

# Backward-compat alias
get_s3_config = get_config



def _storage_class_kwargs() -> dict:
    """``{'StorageClass': 'STANDARD_IA'}`` on real S3, ``{}`` on anything else.

    Same reasoning as ``addressing_style`` above, and the same trigger: a custom
    BUCKET_ENDPOINT_URL means an S3-COMPATIBLE service, not AWS. STANDARD_IA is
    an AWS storage tier; MinIO rejects it outright with ``InvalidStorageClass``,
    which fails the PutObject and takes the whole upload with it — so on a
    MinIO-backed deployment NO document could be ingested at all.

    Omitting the parameter is the correct fallback rather than sending
    ``STANDARD``: the tier is a cost optimisation on AWS and has no meaning on a
    single-node object store, and MinIO applies its own default.
    """
    return ({} if os.getenv('BUCKET_ENDPOINT_URL', '').strip()
            else {'StorageClass': 'STANDARD_IA'})

def upload_file(file_content: bytes, s3_key: str, content_type: str = 'application/octet-stream') -> str:
    """
    Upload file to bucket with environment-based folder structure.
    
    Args:
        file_content: File content as bytes
        s3_key: Object key (path) - will be prefixed with environment
        content_type: MIME type of the file
        
    Returns:
        URL of the uploaded file
    """
    try:
        bucket_name, region = get_config()
        client = _get_client()
        
        # Add environment prefix to S3 key
        env_prefix = get_environment_prefix()
        full_key = f"{env_prefix}/{s3_key.lstrip('/')}"
        
        # Upload file with Standard-IA storage class for cost savings
        # Standard-IA is ideal for infrequently accessed data (30+ days retention)
        client.put_object(
            Bucket=bucket_name,
            Key=full_key,
            Body=file_content,
            ContentType=content_type,
            **_storage_class_kwargs(),
        )
        
        # Return object URL
        endpoint_url = os.getenv('BUCKET_ENDPOINT_URL', '').strip()
        if endpoint_url:
            obj_url = f"{endpoint_url.rstrip('/')}/{bucket_name}/{full_key}"
        else:
            obj_url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{full_key}"
        logger.info(f"✅ Uploaded file: {obj_url}")
        
        return obj_url
        
    except NoCredentialsError:
        logger.error("Bucket credentials not available")
        raise HTTPException(status_code=500, detail="Storage credentials not configured")
    except ClientError as e:
        logger.error(f"Failed to upload file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error uploading file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

# Backward-compat alias
upload_file_to_s3 = upload_file


# Lifecycle for "intermediate" sandbox artifacts (cached web pages, raw
# fetched bytes, OCR pages, etc.). Short-lived: the audit trail can link
# to them so a brief is re-runnable, but they are not user deliverables.
_INTERMEDIATE_TTL_SECONDS = int(os.getenv("ACTION_CHAT_INTERMEDIATE_TTL_SECONDS", str(24 * 3600)))


def upload_intermediate(file_content: bytes, *, user_id: str, job_id: str,
                        filename: str,
                        content_type: str = "application/octet-stream") -> dict:
    """Upload sandbox intermediate object under
    ``{env}/action-chat/{user_id}/{job_id}/intermediate/{filename}`` and
    return ``{key, url, presigned_url, expires_in}``.

    Object is tagged so an S3 lifecycle rule can sweep ``citra-purpose=
    action-chat-intermediate`` after 1 day. Until that rule is configured
    operator-side, presigned-URL expiry alone enforces the contract.
    """
    safe_user = (user_id or "anon").replace("/", "_")
    safe_job = (job_id or "no-job").replace("/", "_")
    safe_name = filename.replace("/", "_") or "blob"
    key = f"action-chat/{safe_user}/{safe_job}/intermediate/{int(datetime.utcnow().timestamp())}_{safe_name}"

    bucket_name, _ = get_config()
    client = _get_client()
    env_prefix = get_environment_prefix()
    full_key = f"{env_prefix}/{key.lstrip('/')}"

    try:
        client.put_object(
            Bucket=bucket_name,
            Key=full_key,
            Body=file_content,
            ContentType=content_type,
            **_storage_class_kwargs(),
            Tagging="citra-purpose=action-chat-intermediate",
            Metadata={
                "citra-user-id": str(user_id),
                "citra-job-id": str(job_id),
                "citra-purpose": "action-chat-intermediate",
            },
        )
    except (ClientError, NoCredentialsError) as e:
        logger.error("intermediate upload failed: %s", e)
        raise HTTPException(status_code=500, detail=f"intermediate upload failed: {e}") from e

    presigned = generate_download_url(key, expiry_seconds=_INTERMEDIATE_TTL_SECONDS)
    return {
        "key": key,
        "url": presigned,
        "presigned_url": presigned,
        "expires_in": _INTERMEDIATE_TTL_SECONDS,
        "content_type": content_type,
        "size_bytes": len(file_content),
    }


def copy_file(source_key: str, dest_key: str) -> bool:
    """
    Copy a file within the same bucket (server-side copy, no data transfer).

    Args:
        source_key: Source key (without environment prefix)
        dest_key:   Destination key (without environment prefix)

    Returns:
        True if successful, False otherwise
    """
    try:
        bucket_name, _ = get_config()
        client = _get_client()
        env_prefix = get_environment_prefix()

        full_source = f"{env_prefix}/{source_key.lstrip('/')}"
        full_dest = f"{env_prefix}/{dest_key.lstrip('/')}"

        client.copy_object(
            Bucket=bucket_name,
            CopySource={'Bucket': bucket_name, 'Key': full_source},
            Key=full_dest,
            **_storage_class_kwargs(),
        )
        logger.info(f"📋 Copied object: {full_source} → {full_dest}")
        return True

    except ClientError as e:
        logger.error(f"Copy failed ({source_key} → {dest_key}): {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error copying object: {e}")
        return False

# Backward-compat alias
copy_file_in_s3 = copy_file


def delete_file(s3_key: str) -> bool:
    """
    Delete file from bucket.
    
    Args:
        s3_key: Object key (path) - will be prefixed with environment if not already
        
    Returns:
        True if successful or file doesn't exist, False otherwise
    """
    try:
        bucket_name, _ = get_config()
        client = _get_client()
        
        # Add environment prefix if not already present
        env_prefix = get_environment_prefix()
        if not s3_key.startswith(f"{env_prefix}/"):
            s3_key = f"{env_prefix}/{s3_key.lstrip('/')}"
        
        # Delete object
        client.delete_object(Bucket=bucket_name, Key=s3_key)
        logger.info(f"✅ Deleted file: {s3_key}")
        
        return True
        
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code == '404' or error_code == 'NoSuchKey':
            logger.info(f"File not found (treated as success): {s3_key}")
            return True
        # RULE #1: a real delete failure must NOT report success — a caller
        # (e.g. GDPR/record deletion) would believe the object is gone when it
        # isn't. Return False so the caller can detect + retry/surface it.
        logger.error(f"Delete FAILED for {s3_key}: {e}")
        return False

    except Exception as e:
        logger.error(f"Delete FAILED (connection) for {s3_key}: {e}")
        return False

# Backward-compat alias
delete_file_from_s3 = delete_file


def list_objects_with_prefix(prefix: str, max_keys: int = 100) -> list:
    """
    List objects in bucket with given prefix for deduplication checks.
    
    Args:
        prefix: Object key prefix to search
        max_keys: Maximum number of keys to return
        
    Returns:
        List of object keys matching the prefix
    """
    try:
        bucket_name, _ = get_config()
        client = _get_client()
        
        # Add environment prefix
        env_prefix = get_environment_prefix()
        full_prefix = f"{env_prefix}/{prefix.lstrip('/')}"
        
        response = client.list_objects_v2(
            Bucket=bucket_name,
            Prefix=full_prefix,
            MaxKeys=max_keys
        )
        
        objects = response.get('Contents', [])
        return [obj['Key'] for obj in objects]
        
    except Exception as e:
        # Fail loud: an S3/list error must NOT be returned as an empty list —
        # callers (sync, dedup-by-hash) treat [] as "bucket empty" and would
        # silently re-upload or drop data. Surface the failure instead.
        logger.error(f"Failed to list objects under prefix {prefix!r}: {e}")
        raise


def find_existing_image_by_hash(prefix: str, content_hash: str) -> str:
    """
    Find existing image with matching content hash.
    
    Args:
        prefix: Object key prefix to search (e.g., pages/user_id/page_id/images/)
        content_hash: MD5 hash of the image content
        
    Returns:
        Object URL if found, None otherwise
    """
    try:
        bucket_name, region = get_config()
        existing_keys = list_objects_with_prefix(prefix)
        
        for key in existing_keys:
            if content_hash in key:
                s3_url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{key}"
                logger.info(f"♻️ Found existing image with matching hash: {s3_url}")
                return s3_url
        
        return None
        
    except Exception as e:
        logger.debug(f"Could not check for existing image: {e}")
        return None


def generate_download_url(s3_key: str, expiry_seconds: int = 1800) -> str:
    """
    Generate presigned URL for object download.
    
    Args:
        s3_key: Object key (path) - will be prefixed with environment if not already
        expiry_seconds: URL expiry time in seconds (default: 1800 = 30 minutes)
        
    Returns:
        Presigned URL for download
    """
    try:
        bucket_name, _ = get_config()
        client = _get_client()
        
        # Add environment prefix if not already present
        env_prefix = get_environment_prefix()
        if not s3_key.startswith(f"{env_prefix}/"):
            s3_key = f"{env_prefix}/{s3_key.lstrip('/')}"
        
        # Generate presigned URL with inline disposition for browser viewing
        presigned_url = client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket_name, 
                'Key': s3_key,
                'ResponseContentDisposition': 'inline'  # Display in browser instead of download
            },
            ExpiresIn=expiry_seconds
        )
        
        logger.info(f"✅ Generated presigned URL for: {s3_key}")
        return presigned_url
        
    except ClientError as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate download URL: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error generating presigned URL: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate download URL: {str(e)}")

# Backward-compat alias
generate_presigned_url = generate_download_url


# ── SmartApp file-upload fallback (blob → bucket → durable ref) ──────────────
# Used by smart-app-service's write path ONLY as the FALLBACK when a source
# exposes no file-capable (semantic_type:"file" / format:"file") write column:
# the file can't go to the MCP, so the platform stores it here and the write
# column receives a durable ref string. Retrieval is access-gated: the detail
# resolver calls /presign (after its own _can_render_app check) to mint a
# short-lived URL at view time (runtime-proxied model — durable + controlled,
# never a leaky long-lived link). Per-app, env-prefixed key layout:
#   <env>/smartapp/<app_spec_id>/<dataset>/<uuid>/<filename>
_SMARTAPP_BLOB_PREFIX = "smartapp-blob://"
_SMARTAPP_BLOB_MAX_BYTES = 15 * 1024 * 1024


def _safe_seg(s: str) -> str:
    import re
    return (re.sub(r"[^A-Za-z0-9._-]", "_", str(s or "")).strip("._-")[:128]) or "x"


@router.post("/api/v2/smartapp-blob/upload")
async def smartapp_blob_upload(
    payload: dict = Body(...),
    user_id: str = Depends(get_secure_user_id),
):
    """Store a SmartApp form upload (a {filename,content_type,data} blob) under a
    per-app, env-prefixed key and return a durable ref. Service-to-service call
    from smart-app-service (user JWT forwarded — shared JWT secret)."""
    import base64
    import uuid
    filename = str(payload.get("filename") or "upload.bin")
    content_type = str(payload.get("content_type") or "application/octet-stream")
    data_b64 = payload.get("data") or ""
    app_id = _safe_seg(payload.get("app_id") or "unknown")
    dataset = _safe_seg(payload.get("dataset") or "misc")
    if not data_b64:
        raise HTTPException(status_code=400, detail="data (base64) is required")
    try:
        content = base64.b64decode(data_b64, validate=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid base64: {e}")
    if len(content) > _SMARTAPP_BLOB_MAX_BYTES:
        raise HTTPException(status_code=400, detail="file too large (max 15MB)")
    key = f"smartapp/{app_id}/{dataset}/{uuid.uuid4().hex}/{_safe_seg(filename)}"
    try:
        upload_file(content, key, content_type=content_type)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — surface, never swallow
        logger.error("[smartapp-blob] store failed key=%s: %s", key, e)
        raise HTTPException(status_code=502, detail=f"blob store failed: {e}")
    logger.info("[smartapp-blob] stored key=%s (%d bytes) by user_id=%s", key, len(content), user_id)
    return {"ref": f"{_SMARTAPP_BLOB_PREFIX}{key}", "filename": filename,
            "content_type": content_type, "size": len(content)}


@router.get("/api/v2/smartapp-blob/presign")
async def smartapp_blob_presign(
    ref: str = Query(...),
    expiry_minutes: int = 30,
    user_id: str = Depends(get_secure_user_id),
):
    """Mint a short-lived presigned URL for a SmartApp blob ref. Access-gated by
    auth; the detail resolver calls this after its own can-render check."""
    key = ref[len(_SMARTAPP_BLOB_PREFIX):] if ref.startswith(_SMARTAPP_BLOB_PREFIX) else ref
    if ".." in key or not key.startswith("smartapp/"):
        raise HTTPException(status_code=400, detail="invalid smartapp blob ref")
    if expiry_minutes <= 0 or expiry_minutes > 60:
        expiry_minutes = 30
    return {"url": generate_download_url(key, expiry_minutes * 60), "ref": ref}


@router.post("/api/v2/smartapp-blob/presign-upload")
async def smartapp_blob_presign_upload(
    payload: dict = Body(...),
    user_id: str = Depends(get_secure_user_id),
):
    """Mint a short-lived presigned S3 **PUT** URL for a DIRECT browser→S3 upload
    of a LARGE file (audio/video) — the base64 ``/upload`` path is for modest
    files only (≤15MB inline). The client PUTs the bytes to ``upload_url`` (its
    ``Content-Type`` header MUST equal ``content_type``), then submits the
    returned ``ref`` as the field value — no second server-side upload. The ref
    resolves to a presigned download via ``/presign`` exactly like the base64
    path (same key layout, env-prefixed at sign time).

    NOTE: a real browser→S3 PUT requires the bucket's CORS to allow PUT from the
    runtime origin — that is an infra config, not code."""
    import uuid
    filename = _safe_seg(payload.get("filename") or "upload.bin")
    content_type = str(payload.get("content_type") or "application/octet-stream")
    app_id = _safe_seg(payload.get("app_id") or "unknown")
    dataset = _safe_seg(payload.get("dataset") or "misc")
    key = f"smartapp/{app_id}/{dataset}/{uuid.uuid4().hex}/{filename}"
    try:
        bucket_name, _ = get_config()
        client = _get_client()
        env_prefix = get_environment_prefix()
        full_key = f"{env_prefix}/{key}"
        upload_url = client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket_name, "Key": full_key, "ContentType": content_type},
            ExpiresIn=900,
        )
    except Exception as e:  # noqa: BLE001 — surface, never swallow
        logger.error("[smartapp-blob] presign-upload failed key=%s: %s", key, e)
        raise HTTPException(status_code=502, detail=f"presign-upload failed: {e}")
    logger.info("[smartapp-blob] presign-upload key=%s by user_id=%s", key, user_id)
    return {
        "ref": f"{_SMARTAPP_BLOB_PREFIX}{key}",
        "upload_url": upload_url,
        "content_type": content_type,
        "expires_in": 900,
    }


@router.get("/s3-image-proxy")
async def s3_image_proxy(
    key: str = Query(..., description="S3 object key to proxy"),
    user_id: str = Depends(get_secure_user_id)
):
    """
    Proxy S3 images through the backend to avoid browser CORS issues.
    The frontend passes the S3 key, backend fetches directly from S3 and streams
    the binary back. This is same-origin for the frontend, so no CORS headers needed.
    
    Used by: globalImageCache.js during export-to-Word/PDF when direct S3 fetch fails.
    
    🔒 SECURITY: Requires authentication - user_id extracted from JWT token
    """
    try:
        # Path traversal protection
        if ".." in key or key.startswith("/"):
            logger.warning(f"❌ [SECURITY] Path traversal attempt in image proxy: {key} by user_id={user_id}")
            raise HTTPException(status_code=400, detail="Invalid S3 key")

        bucket_name, _ = get_config()
        client = _get_client()
        env_prefix = get_environment_prefix()

        # Add environment prefix if not already present
        s3_key = key
        if not s3_key.startswith(f"{env_prefix}/"):
            s3_key = f"{env_prefix}/{s3_key.lstrip('/')}"

        # Validate the key is for images/reports/presentations (not arbitrary files)
        allowed_patterns = ["/images/", "/reports/", "/presentations/", "/printables/", "/profiles/"]
        if not any(p in s3_key for p in allowed_patterns):
            logger.warning(f"❌ [SECURITY] Non-image proxy attempt: {s3_key} by user_id={user_id}")
            raise HTTPException(status_code=403, detail="Only image files can be proxied")

        logger.info(f"📷 [IMAGE_PROXY] user_id={user_id} proxying: {s3_key}")

        response = client.get_object(Bucket=bucket_name, Key=s3_key)
        content_type = response.get('ContentType', 'image/png')
        body = response['Body']

        return StreamingResponse(
            body,
            media_type=content_type,
            headers={
                'Cache-Control': 'public, max-age=3600',
            }
        )

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code == 'NoSuchKey':
            raise HTTPException(status_code=404, detail="Image not found")
        logger.error(f"❌ [IMAGE_PROXY] S3 error: {e}")
        raise HTTPException(status_code=500, detail="Failed to proxy image")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ [IMAGE_PROXY] Error: {e}")
        raise HTTPException(status_code=500, detail="Failed to proxy image")


@router.get("/generate-presigned-url/{folder}/{file}")
async def generate_presigned_url_endpoint(
    folder: str,
    file: str,
    expiry_minutes: int = 30,
    user_id: str = Depends(get_secure_user_id)
):
    """
    Generate presigned URL for S3 object download
    🔒 SECURITY: Requires authentication - user_id extracted from JWT token
    
    Args:
        folder: Folder path within the bucket
        file: File name
        expiry_minutes: URL expiry time in minutes (default: 30)
        user_id: Authenticated user_id from JWT token
    
    Returns:
        dict: Contains the presigned URL and metadata
    """
    try:
        logger.info(f"🔒 Authenticated presigned URL request from user_id={user_id} for {folder}/{file}")
        
        # Path traversal protection
        if ".." in folder or ".." in file:
            logger.warning(f"❌ [SECURITY] Path traversal attempt: {folder}/{file} by user_id={user_id}")
            raise HTTPException(status_code=400, detail="Invalid path characters")
        
        # Validate expiry_minutes
        if expiry_minutes <= 0 or expiry_minutes > 60:
            expiry_minutes = 30
        
        expiry_seconds = expiry_minutes * 60
        s3_key = f"{folder}/{file}"
        
        presigned_url = generate_download_url(s3_key, expiry_seconds)
        
        return {
            "download_url": presigned_url,
            "s3_key": s3_key,
            "folder": folder,
            "file": file,
            "expiry_minutes": expiry_minutes,
            "expires_at": (datetime.utcnow() + timedelta(minutes=expiry_minutes)).isoformat() + "Z"
        }
        
    except Exception as e:
        logger.error(f"Error generating presigned URL: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate presigned URL: {str(e)}"
        )


@router.get("/download/{s3_path:path}", name="download_file_s3")
async def download_file_s3(
    s3_path: str,
    expiry_minutes: int = 30,
    user_id: str = Depends(get_secure_user_id)
):
    """Generate a presigned URL for S3 object and return it as JSON."""
    try:
        normalized_path = s3_path.lstrip("/")
        if not normalized_path:
            raise HTTPException(status_code=400, detail="Invalid S3 path")

        # Path traversal protection
        if ".." in normalized_path:
            logger.warning(f"❌ [SECURITY] Path traversal attempt in download: {s3_path} by user_id={user_id}")
            raise HTTPException(status_code=400, detail="Invalid path characters")

        path_parts = [part for part in normalized_path.split("/") if part]
        if not path_parts:
            raise HTTPException(status_code=400, detail="Invalid S3 path")

        # Validate folder access (security check)
        # First part can be environment (dev/test/prod) or direct folder
        root_folder = path_parts[0]
        allowed_root_folders = {"files", "videos", "personal", "enterprise", "dev", "test", "prod"}
        
        if root_folder not in allowed_root_folders:
            logger.warning(f"❌ [SECURITY] Unauthorized folder access attempt: {root_folder}")
            raise HTTPException(status_code=403, detail="Folder access not permitted")

        logger.info(f"🔒 [DOWNLOAD] user_id={user_id} requesting {normalized_path}")

        if expiry_minutes <= 0 or expiry_minutes > 60:
            expiry_minutes = 30

        expiry_seconds = expiry_minutes * 60
        presigned_url = generate_download_url(normalized_path, expiry_seconds)

        response_payload = {
            "download_url": presigned_url,
            "s3_key": normalized_path,
            "folder": root_folder,
            "file": path_parts[-1],
            "expires_at": (datetime.utcnow() + timedelta(minutes=expiry_minutes)).isoformat() + "Z",
        }

        json_headers = {"Access-Control-Allow-Origin": DEFAULT_DOWNLOAD_CORS_ORIGIN}
        if DEFAULT_DOWNLOAD_CORS_ORIGIN != "*":
            json_headers["Vary"] = "Origin"

        return JSONResponse(content=response_payload, headers=json_headers)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"❌ [DOWNLOAD] Error generating presigned URL: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate presigned URL for download: {exc}",
        )


@router.get("/api/documents/{document_id}/download", name="download_document_by_id")
async def download_document_by_id(
    document_id: str,
    expiry_minutes: int = 30,
    user_id: str = Depends(get_secure_user_id)
):
    """
    Generate presigned S3 URL directly from document ID.
    Simplified endpoint that eliminates need for frontend URL parsing.
    
    Args:
        document_id: Document UUID
        expiry_minutes: URL expiry time (default 30 minutes)
        user_id: Authenticated user (from JWT)
        
    Returns:
        {
            "download_url": "presigned_s3_url",
            "document_id": "document_id",
            "filename": "original_filename.pdf",
            "expires_at": "2024-11-12T12:30:00Z"
        }
    """
    try:
        # Import MongoDB manager
        from citra_mongo import get_sync_database
        
        logger.info(f"🔒 [DOWNLOAD_BY_ID] user_id={user_id} requesting document_id={document_id}")
        
        # Get document metadata from files collection
        db = get_sync_database()
        files_collection = db.files
        
        file_record = files_collection.find_one({
            "_id": document_id,
            "user_id": user_id  # Security: Ensure user owns document
        })
        
        # If not found by owner, check shared access via vault
        if not file_record:
            shared_record = files_collection.find_one({"_id": document_id})
            if shared_record:
                doc_folder_id = shared_record.get("folder_id")
                if doc_folder_id:
                    try:
                        from services.authorization_service import get_authorization_service
                        auth_service = get_authorization_service()
                        access_result = await auth_service.check_access(
                            user_id=user_id,
                            resource_id=doc_folder_id,
                            resource_type="vault",
                            required_permission="read"
                        )
                        if access_result.get("allowed"):
                            logger.info(f"✅ [DOWNLOAD_BY_ID] Shared access granted for document {document_id} via vault {doc_folder_id}")
                            file_record = shared_record
                    except Exception as auth_err:
                        logger.warning(f"⚠️ [DOWNLOAD_BY_ID] Error checking shared access: {auth_err}")
        
        if not file_record:
            # 🌐 INTERNET RESEARCH FALLBACK: Check document_chunked collection
            # Internet research docs (from presentation generator) are stored only
            # in document_chunked, not in the files collection or S3
            logger.info(f"🔍 [DOWNLOAD_BY_ID] Checking document_chunked for: {document_id}")
            chunks_collection = db.document_chunked
            chunk_docs = list(chunks_collection.find({
                "document_id": document_id,
                "user_id": user_id
            }).sort("chunk_index", 1))
            
            if chunk_docs:
                logger.info(f"✅ [DOWNLOAD_BY_ID] Found {len(chunk_docs)} chunks in document_chunked for: {document_id}")
                # Reconstruct text from chunks and serve as downloadable .txt
                full_text = "\n\n".join(
                    chunk.get("text", "") or chunk.get("metadata", {}).get("text", "")
                    for chunk in chunk_docs
                )
                topic = chunk_docs[0].get("topic_or_filename", "") or chunk_docs[0].get("metadata", {}).get("topic_or_filename", "") or document_id
                filename = f"{topic}.txt" if not topic.endswith(".txt") else topic
                
                # Encode text as a base64 data URL so the frontend can trigger a download
                import base64
                encoded = base64.b64encode(full_text.encode('utf-8')).decode('ascii')
                data_url = f"data:text/plain;base64,{encoded}"
                
                response_payload = {
                    "download_url": data_url,
                    "document_id": document_id,
                    "filename": filename,
                }
                
                json_headers = {"Access-Control-Allow-Origin": DEFAULT_DOWNLOAD_CORS_ORIGIN}
                if DEFAULT_DOWNLOAD_CORS_ORIGIN != "*":
                    json_headers["Vary"] = "Origin"
                
                return JSONResponse(content=response_payload, headers=json_headers)
            
            logger.warning(f"❌ [DOWNLOAD_BY_ID] Document not found or access denied: {document_id}")
            raise HTTPException(status_code=404, detail="Document not found or access denied")
        
        s3_url = file_record.get("s3_url")
        if not s3_url:
            logger.warning(f"❌ [DOWNLOAD_BY_ID] No S3 URL in document: {document_id}")
            raise HTTPException(status_code=404, detail="Document file not found in storage")
        
        # Extract S3 key from stored URL
        if ".amazonaws.com/" in s3_url:
            s3_key = s3_url.split(".amazonaws.com/")[-1]
        elif "s3://" in s3_url:
            s3_key = s3_url.split("s3://", 1)[-1].split("/", 1)[-1]
        else:
            s3_key = s3_url
        
        # Validate expiry time
        if expiry_minutes <= 0 or expiry_minutes > 60:
            expiry_minutes = 30
        
        expiry_seconds = expiry_minutes * 60
        
        # Generate presigned URL
        presigned_url = generate_presigned_url(s3_key, expiry_seconds)
        
        # Get filename (with fallback)
        filename = file_record.get("filename") or file_record.get("topic_or_filename") or f"document_{document_id}"
        
        logger.info(f"✅ [DOWNLOAD_BY_ID] Generated presigned URL for document_id={document_id}")
        
        response_payload = {
            "download_url": presigned_url,
            "document_id": document_id,
            "filename": filename,
            "expires_at": (datetime.utcnow() + timedelta(minutes=expiry_minutes)).isoformat() + "Z"
        }
        
        json_headers = {"Access-Control-Allow-Origin": DEFAULT_DOWNLOAD_CORS_ORIGIN}
        if DEFAULT_DOWNLOAD_CORS_ORIGIN != "*":
            json_headers["Vary"] = "Origin"
        
        return JSONResponse(content=response_payload, headers=json_headers)
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"❌ [DOWNLOAD_BY_ID] Error generating presigned URL: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate presigned URL for download: {exc}",
        )


@router.get("/api/transcripts/{transcript_id}/download", name="download_transcript_by_id")
async def download_transcript_by_id(
    transcript_id: str,
    expiry_minutes: int = 30,
    user_id: str = Depends(get_secure_user_id)
):
    """
    Generate presigned S3 URL directly from transcript ID.
    Works for both audio and video transcripts.
    
    Args:
        transcript_id: Transcript UUID
        expiry_minutes: URL expiry time (default 30 minutes)
        user_id: Authenticated user (from JWT)
        
    Returns:
        {
            "download_url": "presigned_s3_url",
            "transcript_id": "transcript_id",
            "filename": "original_filename.webm",
            "type": "audio" or "video",
            "expires_at": "2024-11-12T12:30:00Z"
        }
    """
    try:
        # Import MongoDB manager
        from citra_mongo import get_sync_database
        
        logger.info(f"🔒 [DOWNLOAD_TRANSCRIPT] user_id={user_id} requesting transcript_id={transcript_id}")
        
        db = get_sync_database()
        
        # Try audio_transcripts collection first
        transcript_record = db.audio_transcripts.find_one({
            "_id": transcript_id,
            "user_id": user_id  # Security: Ensure user owns transcript
        })
        
        media_type = "audio"
        
        # If not found, try video transcripts
        if not transcript_record:
            transcript_record = db.video_transcripts.find_one({
                "_id": transcript_id,
                "user_id": user_id
            })
            media_type = "video"
        
        # If still not found, check shared vault access
        if not transcript_record:
            # Try finding transcript without user_id filter
            shared_record = db.audio_transcripts.find_one({"_id": transcript_id})
            media_type = "audio"
            if not shared_record:
                shared_record = db.video_transcripts.find_one({"_id": transcript_id})
                media_type = "video"
            
            if shared_record:
                doc_folder_id = shared_record.get("folder_id")
                if doc_folder_id:
                    try:
                        from services.authorization_service import get_authorization_service
                        auth_service = get_authorization_service()
                        access_result = await auth_service.check_access(
                            user_id=user_id,
                            resource_id=doc_folder_id,
                            resource_type="vault",
                            required_permission="read"
                        )
                        if access_result.get("allowed"):
                            logger.info(f"✅ [DOWNLOAD_TRANSCRIPT] Shared access granted for transcript {transcript_id} via vault {doc_folder_id}")
                            transcript_record = shared_record
                    except Exception as auth_err:
                        logger.warning(f"⚠️ [DOWNLOAD_TRANSCRIPT] Error checking shared access: {auth_err}")
                else:
                    logger.warning(f"⚠️ [DOWNLOAD_TRANSCRIPT] Shared transcript {transcript_id} has no folder_id — skipping auth check")
        
        if not transcript_record:
            logger.warning(f"❌ [DOWNLOAD_TRANSCRIPT] Transcript not found or access denied: {transcript_id}")
            raise HTTPException(status_code=404, detail="Transcript not found or access denied")
        
        # Get media URL based on type
        s3_url = transcript_record.get("video_url") if media_type == "video" else transcript_record.get("audio_url")
        
        if not s3_url:
            logger.warning(f"❌ [DOWNLOAD_TRANSCRIPT] No media URL in transcript: {transcript_id}")
            raise HTTPException(status_code=404, detail="Transcript media file not found in storage")
        
        # Extract S3 key from stored URL
        if ".amazonaws.com/" in s3_url:
            s3_key = s3_url.split(".amazonaws.com/")[-1]
        elif "s3://" in s3_url:
            s3_key = s3_url.split("s3://", 1)[-1].split("/", 1)[-1]
        else:
            s3_key = s3_url
        
        # Validate expiry time
        if expiry_minutes <= 0 or expiry_minutes > 60:
            expiry_minutes = 30
        
        expiry_seconds = expiry_minutes * 60
        
        # Generate presigned URL
        presigned_url = generate_presigned_url(s3_key, expiry_seconds)
        
        # Get filename (with fallback and proper extension)
        topic = transcript_record.get("topic") or f"transcript_{transcript_id}"
        
        # Determine extension from S3 key if topic doesn't have one
        if '.' not in topic:
            # Extract extension from S3 key
            extension = ".webm"  # default
            if '.' in s3_key:
                extension = '.' + s3_key.split('.')[-1]
            filename = f"{topic}{extension}"
        else:
            filename = topic
        
        logger.info(f"✅ [DOWNLOAD_TRANSCRIPT] Generated presigned URL for transcript_id={transcript_id}, type={media_type}")
        
        response_payload = {
            "download_url": presigned_url,
            "transcript_id": transcript_id,
            "filename": filename,
            "type": media_type,
            "expires_at": (datetime.utcnow() + timedelta(minutes=expiry_minutes)).isoformat() + "Z"
        }
        
        json_headers = {"Access-Control-Allow-Origin": DEFAULT_DOWNLOAD_CORS_ORIGIN}
        if DEFAULT_DOWNLOAD_CORS_ORIGIN != "*":
            json_headers["Vary"] = "Origin"
        
        return JSONResponse(content=response_payload, headers=json_headers)
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"❌ [DOWNLOAD_TRANSCRIPT] Error generating presigned URL: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate presigned URL for transcript: {exc}",
        )


@router.get("/health")
async def health_check():
    """Health check endpoint for storage service"""
    try:
        bucket_name, region = get_config()
        env_prefix = get_environment_prefix()
        return {
            "status": "healthy",
            "service": "bucket_storage",
            "bucket": bucket_name,
            "region": region,
            "environment": env_prefix,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "service": "s3_storage",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
