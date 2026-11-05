"""Server-only Supabase Storage operations for the private report bucket."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import BinaryIO

from storage3.exceptions import StorageApiError
from storage3.utils import StorageException
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from app.config import get_settings

logger = logging.getLogger(__name__)
BUCKET_NAME = "annual-reports"

_bucket_verified_at: dict[tuple[int, str], float] = {}


class StorageOperationError(Exception):
    """A storage operation failed; the original provider response is not exposed."""

    def __init__(self, operation: str, status_code: int | None = None) -> None:
        super().__init__(operation)
        self.operation = operation
        self.status_code = status_code


def _provider_status(exc: Exception) -> int | None:
    status = getattr(exc, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def reset_storage_client() -> None:
    """Clear cached storage client and verified bucket states (useful on credential rotation)."""
    _get_client.cache_clear()
    _bucket_verified_at.clear()


@lru_cache(maxsize=1)
def _get_client() -> Client:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise StorageOperationError("configuration")
    try:
        return create_client(
            settings.supabase_url,
            settings.supabase_service_role_key,
            options=SyncClientOptions(auto_refresh_token=False, persist_session=False),
        )
    except Exception as exc:
        logger.error("Supabase Storage client initialization failed")
        raise StorageOperationError("client_initialization") from exc


def _bucket(client: Client):
    import time
    settings = get_settings()
    storage = client.storage
    cache_key = (id(client), BUCKET_NAME)
    now = time.monotonic()

    if now - _bucket_verified_at.get(cache_key, 0.0) < 300.0:
        return storage.from_(BUCKET_NAME)

    try:
        bucket_info = storage.get_bucket(BUCKET_NAME)
    except StorageApiError as exc:
        if _provider_status(exc) != 404:
            logger.error("Supabase Storage bucket lookup failed", extra={"status": _provider_status(exc)})
            raise StorageOperationError("bucket_lookup", _provider_status(exc)) from exc
        try:
            storage.create_bucket(
                BUCKET_NAME,
                options={
                    "public": False,
                    "file_size_limit": settings.max_pdf_size_bytes,
                    "allowed_mime_types": ["application/pdf"],
                },
            )
            bucket_info = storage.get_bucket(BUCKET_NAME)
        except StorageException as create_exc:
            # Another API process may have provisioned the bucket concurrently.
            try:
                bucket_info = storage.get_bucket(BUCKET_NAME)
            except StorageException:
                logger.error("Supabase Storage bucket provisioning failed")
                raise StorageOperationError("bucket_provisioning") from create_exc

    if getattr(bucket_info, "public", True):
        logger.error("Supabase report bucket is public; refusing report storage access")
        raise StorageOperationError("bucket_configuration")
    configured_limit = getattr(bucket_info, "file_size_limit", None)
    if configured_limit is not None and configured_limit < settings.max_pdf_size_bytes:
        logger.error("Supabase report bucket size limit is below application limit")
        raise StorageOperationError("bucket_configuration")
    allowed_types = getattr(bucket_info, "allowed_mime_types", None)
    if allowed_types is not None and "application/pdf" not in allowed_types:
        logger.error("Supabase report bucket does not allow PDF files")
        raise StorageOperationError("bucket_configuration")
    _bucket_verified_at[cache_key] = now
    return storage.from_(BUCKET_NAME)


class ReportStorage:
    """Storage adapter; callers construct paths from verified and generated IDs."""

    def __init__(self, client: Client | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Client:
        return self._client or _get_client()

    def upload(self, path: str, stream: BinaryIO) -> None:
        try:
            _bucket(self.client).upload(
                path,
                stream,
                file_options={"content-type": "application/pdf", "upsert": "false"},
            )
        except StorageOperationError:
            raise
        except Exception as exc:
            logger.error("Supabase PDF upload failed", extra={"status": _provider_status(exc), "error": str(exc)})
            raise StorageOperationError("upload", _provider_status(exc)) from exc

    def metadata(self, path: str) -> dict[str, object]:
        try:
            info = _bucket(self.client).info(path)
            if isinstance(info, dict):
                return {"name": info.get("name"), "metadata": info.get("metadata")}
            return {"name": getattr(info, "name", None), "metadata": getattr(info, "metadata", None)}
        except StorageOperationError:
            raise
        except Exception as exc:
            logger.error("Supabase PDF metadata lookup failed", extra={"status": _provider_status(exc), "error": str(exc)})
            raise StorageOperationError("metadata", _provider_status(exc)) from exc

    def download(self, path: str) -> bytes:
        """Download an object selected from an owned report row, never a request path."""
        try:
            payload = _bucket(self.client).download(path)
            if not isinstance(payload, bytes):
                payload = bytes(payload)
            return payload
        except StorageOperationError:
            raise
        except Exception as exc:
            logger.error("Supabase PDF download failed", extra={"status": _provider_status(exc), "error": str(exc)})
            raise StorageOperationError("download", _provider_status(exc)) from exc

    def create_signed_download_url(self, path: str) -> tuple[str, int]:
        try:
            ttl = get_settings().report_download_url_ttl_seconds
            result = _bucket(self.client).create_signed_url(path, ttl)
            url = result.get("signedURL") or result.get("signedUrl")
            if not isinstance(url, str) or not url:
                raise StorageOperationError("signed_url")
            return url, ttl
        except StorageOperationError:
            raise
        except Exception as exc:
            logger.error("Supabase signed URL creation failed", extra={"status": _provider_status(exc), "error": str(exc)})
            raise StorageOperationError("signed_url", _provider_status(exc)) from exc

    def delete(self, path: str) -> None:
        try:
            _bucket(self.client).remove([path])
        except StorageOperationError:
            raise
        except StorageApiError as exc:
            # Storage may return not-found when a prior partial cleanup already succeeded.
            if _provider_status(exc) == 404:
                return
            logger.error("Supabase PDF deletion failed", extra={"status": _provider_status(exc), "error": str(exc)})
            raise StorageOperationError("delete", _provider_status(exc)) from exc
        except Exception as exc:
            logger.error("Supabase PDF deletion failed", extra={"status": _provider_status(exc), "error": str(exc)})
            raise StorageOperationError("delete", _provider_status(exc)) from exc

    def exists(self, path: str) -> bool:
        try:
            _bucket(self.client).info(path)
            return True
        except StorageOperationError:
            raise
        except StorageApiError as exc:
            if _provider_status(exc) == 404:
                return False
            logger.error("Supabase PDF existence check failed", extra={"status": _provider_status(exc), "error": str(exc)})
            raise StorageOperationError("exists", _provider_status(exc)) from exc
        except Exception as exc:
            logger.error("Supabase PDF existence check failed", extra={"status": _provider_status(exc), "error": str(exc)})
            raise StorageOperationError("exists", _provider_status(exc)) from exc


storage = ReportStorage()
