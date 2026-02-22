from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import quote

from google.api_core.exceptions import NotFound
from google.cloud import storage
from google.oauth2 import service_account


# Defaults can be overridden via env vars without touching code
DEFAULT_BUCKET = os.getenv("BUCKET_NAME") or os.getenv("API_STORAGE_BUCKET", "api_information_storage")
DEFAULT_KEYFILE = os.getenv("API_BUCKET_KEY_FILE", "secrets/api_bucket_db_key.json")


def _credentials():
    """
    Return credentials for the storage client. Prefer explicit service-account
    key file (the project ships with `secrets/api_bucket_db_key.json`).
    Fallback to Application Default Credentials if the file is missing.
    """
    path = DEFAULT_KEYFILE
    try:
        if path and os.path.exists(path):
            return service_account.Credentials.from_service_account_file(path)
    except Exception:
        pass
    # Fallback to ADC (e.g., when running on GCP or when GAC is set)
    return None


def storage_client() -> storage.Client:
    creds = _credentials()
    if creds is not None:
        return storage.Client(credentials=creds, project=creds.project_id)
    return storage.Client()  # ADC


def get_bucket(name: Optional[str] = None) -> storage.Bucket:
    client = storage_client()
    bucket_name = name or DEFAULT_BUCKET
    return client.bucket(bucket_name)


def bucket_name() -> str:
    return DEFAULT_BUCKET


def media_path(blob_name: str) -> str:
    normalized = str(blob_name or "").strip().lstrip("/")
    return f"/media/{quote(normalized, safe='/')}"


def upload_bytes(data: bytes, blob_name: str, *, content_type: Optional[str] = None, cache_seconds: int = 0) -> str:
    bucket = get_bucket()
    blob = bucket.blob(blob_name)
    if cache_seconds:
        blob.cache_control = f"public, max-age={int(cache_seconds)}"
    blob.upload_from_string(data, content_type=content_type)
    return blob.name


def download_with_metadata(blob_name: str) -> tuple[bytes, Optional[str]]:
    client = storage_client()
    bucket = client.bucket(DEFAULT_BUCKET)
    blob = bucket.blob(blob_name)
    try:
        blob.reload(client=client)
        payload = blob.download_as_bytes(client=client)
    except NotFound:
        raise FileNotFoundError(blob_name)
    return payload, blob.content_type


def blob_http_metadata(blob_name: str) -> tuple[Optional[str], Optional[str], Optional[datetime]]:
    """
    Return (content_type, etag, updated_at_utc) for a blob without downloading payload bytes.
    Raises FileNotFoundError when the blob does not exist.
    """
    client = storage_client()
    bucket = client.bucket(DEFAULT_BUCKET)
    blob = bucket.blob(blob_name)
    try:
        blob.reload(client=client)
    except NotFound:
        raise FileNotFoundError(blob_name)
    return blob.content_type, blob.etag, blob.updated


def upload_fileobj(fileobj, blob_name: str, *, content_type: Optional[str] = None, cache_seconds: int = 0) -> str:
    data = fileobj.read()
    if hasattr(fileobj, "seek"):
        try:
            fileobj.seek(0)
        except Exception:
            pass
    return upload_bytes(data, blob_name, content_type=content_type, cache_seconds=cache_seconds)


def download_bytes(blob_name: str) -> bytes:
    client = storage_client()
    bucket = client.bucket(DEFAULT_BUCKET)
    blob = bucket.blob(blob_name)
    try:
        return blob.download_as_bytes(client=client)
    except NotFound:
        raise FileNotFoundError(blob_name)


def delete_prefix(prefix: str) -> int:
    """Delete all blobs under the prefix. Returns count deleted."""
    client = storage_client()
    bucket = get_bucket()
    deleted = 0
    for blob in client.list_blobs(bucket, prefix=prefix):
        try:
            blob.delete()
            deleted += 1
        except Exception:
            pass
    return deleted


def blob_exists(blob_name: str) -> bool:
    bucket = get_bucket()
    return bucket.blob(blob_name).exists(storage_client())


def is_name_taken(uid: str, name: str) -> bool:
    """Return True if any object exists under `<uid>/<name>/` prefix."""
    client = storage_client()
    bucket = get_bucket()
    prefix = f"{uid}/{name}/"
    it = client.list_blobs(bucket, prefix=prefix, max_results=1)
    for _ in it:
        return True
    return False


def copy_blob(src_blob: str, dst_blob: str, *, delete_src: bool = False) -> None:
    bucket = get_bucket()
    src = bucket.blob(src_blob)
    bucket.copy_blob(src, bucket, dst_blob)
    if delete_src:
        try:
            src.delete()
        except Exception:
            pass


def iter_user_docs(uid: str) -> Iterable[Dict[str, Any]]:
    """
    Yield parsed JSON docs for a user by scanning `<uid>/*/doc.json`.
    """
    client = storage_client()
    bucket = get_bucket()
    prefix = f"{uid}/"
    for blob in client.list_blobs(bucket, prefix=prefix):
        name = blob.name or ""
        if not name.endswith("/doc.json"):
            continue
        try:
            raw = blob.download_as_bytes()
            doc = json.loads(raw.decode("utf-8"))
            yield doc
        except Exception:
            # ignore malformed docs
            continue


def find_doc_by_id(uid: str, api_id: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """Return (doc, blob_name) for the given id within user's folder."""
    client = storage_client()
    bucket = get_bucket()
    prefix = f"{uid}/"
    for blob in client.list_blobs(bucket, prefix=prefix):
        name = blob.name or ""
        if not name.endswith("/doc.json"):
            continue
        try:
            raw = blob.download_as_bytes()
            doc = json.loads(raw.decode("utf-8"))
            if str(doc.get("id")) == str(api_id):
                return doc, name
        except Exception:
            continue
    return None


def find_doc_by_name(uid: str, api_name: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """Return (doc, blob_name) for the given API folder name."""
    client = storage_client()
    bucket = get_bucket()
    blob_name = f"{uid}/{api_name}/doc.json"
    blob = bucket.blob(blob_name)
    if not blob.exists(client):
        return None
    try:
        raw = blob.download_as_bytes()
        doc = json.loads(raw.decode("utf-8"))
        return doc, blob_name
    except Exception:
        return None


def signed_url(blob_name: str, minutes: int = 15) -> Optional[str]:
    """Generate a V4 signed URL for GET; return None on failure."""
    try:
        bucket = get_bucket()
        blob = bucket.blob(blob_name)
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=max(1, int(minutes))),
            method="GET",
        )
    except Exception:
        return None
