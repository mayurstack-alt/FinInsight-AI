from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from storage3.exceptions import StorageApiError

from app.config import get_settings
from app.modules.storage import service as storage_service
from app.modules.storage.service import (
    BUCKET_NAME,
    ReportStorage,
    StorageOperationError,
    reset_storage_client,
)


def storage_client(*, is_public: bool = False):
    client = Mock()
    bucket = Mock()
    client.storage.get_bucket.return_value = SimpleNamespace(
        public=is_public,
        file_size_limit=25 * 1024 * 1024,
        allowed_mime_types=["application/pdf"],
    )
    client.storage.from_.return_value = bucket
    return client, bucket


def test_private_bucket_upload_uses_pdf_type_and_never_upserts() -> None:
    client, bucket = storage_client()
    adapter = ReportStorage(client)
    from io import BytesIO

    stream = BytesIO(b"%PDF-test")
    adapter.upload("user/report/annual.pdf", stream)
    client.storage.get_bucket.assert_called_once_with(BUCKET_NAME)
    client.storage.from_.assert_called_once_with(BUCKET_NAME)
    bucket.upload.assert_called_once_with(
        "user/report/annual.pdf",
        stream,
        file_options={"content-type": "application/pdf", "upsert": "false"},
    )


def test_server_client_does_not_persist_or_refresh_auth_sessions(monkeypatch) -> None:
    client = Mock()
    factory = Mock(return_value=client)
    monkeypatch.setattr(storage_service, "get_settings", lambda: SimpleNamespace(
        supabase_url="https://project.example",
        supabase_service_role_key="server-placeholder",
    ))
    monkeypatch.setattr(storage_service, "create_client", factory)
    storage_service._get_client.cache_clear()
    try:
        assert storage_service._get_client() is client
        options = factory.call_args.kwargs["options"]
        assert options.persist_session is False
        assert options.auto_refresh_token is False
    finally:
        storage_service._get_client.cache_clear()


def test_public_bucket_fails_closed() -> None:
    client, _bucket = storage_client(is_public=True)
    with pytest.raises(StorageOperationError) as error:
        ReportStorage(client).exists("user/report/file.pdf")
    assert error.value.operation == "bucket_configuration"


def test_missing_bucket_is_created_private_with_pdf_limits() -> None:
    client, _bucket = storage_client()
    missing = StorageApiError("not found", "Bucket not found", 404)
    client.storage.get_bucket.side_effect = [missing, SimpleNamespace(
        public=False,
        file_size_limit=get_settings().max_pdf_size_bytes,
        allowed_mime_types=["application/pdf"],
    )]
    ReportStorage(client).exists("user/report/file.pdf")
    client.storage.create_bucket.assert_called_once_with(
        BUCKET_NAME,
        options={
            "public": False,
            "file_size_limit": get_settings().max_pdf_size_bytes,
            "allowed_mime_types": ["application/pdf"],
        },
    )


def test_signed_download_url_has_short_lifetime() -> None:
    client, bucket = storage_client()
    bucket.create_signed_url.return_value = {"signedURL": "https://storage.example/short"}
    url, ttl = ReportStorage(client).create_signed_download_url("user/report/file.pdf")
    assert url == "https://storage.example/short"
    assert ttl == 300
    bucket.create_signed_url.assert_called_once_with("user/report/file.pdf", 300)


def test_delete_and_metadata_use_supplied_object_path() -> None:
    client, bucket = storage_client()
    bucket.info.return_value = {"name": "file.pdf", "metadata": {"mimetype": "application/pdf"}}
    adapter = ReportStorage(client)
    assert adapter.exists("owner/report/file.pdf")
    assert adapter.metadata("owner/report/file.pdf")["name"] == "file.pdf"
    adapter.delete("owner/report/file.pdf")
    bucket.remove.assert_called_once_with(["owner/report/file.pdf"])


def test_download_uses_database_selected_path() -> None:
    client, bucket = storage_client()
    bucket.download.return_value = b"%PDF-example"
    result = ReportStorage(client).download("owner/report/file.pdf")
    assert result == b"%PDF-example"
    bucket.download.assert_called_once_with("owner/report/file.pdf")


def test_reset_storage_client_clears_caches() -> None:
    reset_storage_client()
    assert storage_service._bucket_verified_at == {}

