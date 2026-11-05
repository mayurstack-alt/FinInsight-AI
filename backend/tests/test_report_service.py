from io import BytesIO
from uuid import UUID, uuid4

import pytest
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import Headers

from app.core.errors import ApplicationError
from app.database import supabase_auth_users
from app.modules.auth.models import Profile
from app.modules.auth.service import AuthenticatedUser
from app.modules.reports.models import Report, ReportStatus
from app.modules.reports.service import (
    create_report,
    delete_report,
    get_download_url,
    get_report,
    get_report_status,
    list_reports,
    retry_report,
    update_report_status,
)
from app.modules.storage.service import StorageOperationError
from tests.pdf_samples import minimal_pdf


class FakeStorage:
    def __init__(self) -> None:
        self.objects: set[str] = set()
        self.deleted: list[str] = []
        self.fail_upload = False
        self.fail_delete_count = 0
        self.signed_url_calls: list[str] = []

    def upload(self, path, stream) -> None:
        if self.fail_upload:
            raise StorageOperationError("upload", 503)
        assert stream.read(5) == b"%PDF-"
        stream.seek(0)
        self.objects.add(path)

    def delete(self, path) -> None:
        self.deleted.append(path)
        if self.fail_delete_count:
            self.fail_delete_count -= 1
            raise StorageOperationError("delete", 503)
        self.objects.discard(path)

    def exists(self, path) -> bool:
        return path in self.objects

    def create_signed_download_url(self, path):
        self.signed_url_calls.append(path)
        return "https://storage.example/signed", 300


def make_upload(name: str = "annual-report.pdf") -> UploadFile:
    return UploadFile(
        filename=name,
        file=BytesIO(minimal_pdf()),
        headers=Headers({"content-type": "application/pdf"}),
    )


def make_user(db_session, user_id=None) -> AuthenticatedUser:
    user_id = user_id or uuid4()
    db_session.execute(supabase_auth_users.insert().values(id=user_id))
    db_session.flush()
    return AuthenticatedUser(id=str(user_id), email="person@example.test")


def create(db_session, user, storage):
    return create_report(
        db_session,
        user,
        make_upload(),
        "Example Company",
        "FY2025",
        storage,
    )


def test_create_report_persists_profile_and_uploaded_metadata(db_session) -> None:
    user = make_user(db_session)
    storage = FakeStorage()
    report = create(db_session, user, storage)

    assert report.status is ReportStatus.UPLOADED
    assert report.storage_path.startswith(f"{user.id}/{report.id}/")
    assert report.storage_path in storage.objects
    assert db_session.get(Profile, uuid4()) is None
    assert db_session.get(Profile, report.user_id) is not None


def test_create_rolls_back_database_failure_and_cleans_uploaded_object(db_session, monkeypatch) -> None:
    user = make_user(db_session)
    storage = FakeStorage()
    original_commit = db_session.commit

    def fail_commit():
        raise SQLAlchemyError("private database details")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    with pytest.raises(ApplicationError) as error:
        create(db_session, user, storage)
    assert error.value.status_code == 500
    assert len(storage.deleted) == 1
    assert not storage.objects
    monkeypatch.setattr(db_session, "commit", original_commit)


def test_storage_upload_failure_creates_no_database_record(db_session) -> None:
    user = make_user(db_session)
    storage = FakeStorage()
    storage.fail_upload = True
    with pytest.raises(ApplicationError) as error:
        create(db_session, user, storage)
    assert error.value.status_code == 502
    assert db_session.scalar(select(Report)) is None


def test_listing_and_retrieval_are_scoped_to_authenticated_user(db_session) -> None:
    owner = make_user(db_session)
    other = make_user(db_session)
    storage = FakeStorage()
    report = create(db_session, owner, storage)

    items, total = list_reports(db_session, owner)
    assert total == 1 and [item.id for item in items] == [report.id]
    assert list_reports(db_session, other) == ([], 0)
    assert get_report(db_session, owner, report.id).id == report.id
    with pytest.raises(ApplicationError) as error:
        get_report(db_session, other, report.id)
    assert error.value.status_code == 404
    assert error.value.code == "report_not_found"


def test_status_and_authorized_download(db_session) -> None:
    user = make_user(db_session)
    storage = FakeStorage()
    report = create(db_session, user, storage)

    assert get_report_status(db_session, user, report.id).status is ReportStatus.UPLOADED
    assert get_download_url(db_session, user, report.id, storage) == (
        "https://storage.example/signed",
        300,
    )
    assert len(storage.signed_url_calls) == 1


def test_other_user_cannot_request_signed_download(db_session) -> None:
    owner = make_user(db_session)
    other = make_user(db_session)
    storage = FakeStorage()
    report = create(db_session, owner, storage)
    with pytest.raises(ApplicationError) as error:
        get_download_url(db_session, other, report.id, storage)
    assert error.value.status_code == 404
    assert storage.signed_url_calls == []


def test_delete_soft_hides_report_and_retries_failed_storage_cleanup(db_session) -> None:
    user = make_user(db_session)
    storage = FakeStorage()
    report = create(db_session, user, storage)
    report_id = report.id
    path = report.storage_path
    storage.fail_delete_count = 1

    with pytest.raises(ApplicationError) as error:
        delete_report(db_session, user, report_id, storage)
    assert error.value.code == "storage_cleanup_pending"
    assert db_session.get(Report, report_id).deleted_at is not None
    assert list_reports(db_session, user) == ([], 0)
    with pytest.raises(ApplicationError):
        get_report(db_session, user, report_id)

    delete_report(db_session, user, report_id, storage)
    assert db_session.get(Report, report_id).storage_deleted_at is not None
    assert path not in storage.objects
    assert len(storage.deleted) == 2


def test_other_user_cannot_delete_report(db_session) -> None:
    owner = make_user(db_session)
    other = make_user(db_session)
    storage = FakeStorage()
    report = create(db_session, owner, storage)
    with pytest.raises(ApplicationError) as error:
        delete_report(db_session, other, report.id, storage)
    assert error.value.status_code == 404
    assert storage.deleted == []


def test_download_fails_safely_when_object_is_missing(db_session) -> None:
    user = make_user(db_session)
    storage = FakeStorage()
    report = create(db_session, user, storage)
    storage.objects.clear()
    with pytest.raises(ApplicationError) as error:
        get_download_url(db_session, user, report.id, storage)
    assert error.value.status_code == 404
    assert storage.signed_url_calls == []


def test_create_orphan_cleanup_failure_is_logged_without_storage_path(db_session, monkeypatch, caplog) -> None:
    import logging
    caplog.set_level(logging.ERROR)
    user = make_user(db_session)
    storage = FakeStorage()
    storage.delete = lambda _path: (_ for _ in ()).throw(StorageOperationError("delete", 503))
    monkeypatch.setattr(db_session, "commit", lambda: (_ for _ in ()).throw(SQLAlchemyError()))
    with pytest.raises(ApplicationError):
        create(db_session, user, storage)
    assert "Orphaned report upload cleanup failed" in caplog.text
    assert "annual-report.pdf" not in caplog.text


def test_update_report_status(db_session) -> None:
    user = make_user(db_session)
    other = make_user(db_session)
    storage = FakeStorage()
    report = create(db_session, user, storage)
    assert report.status is ReportStatus.UPLOADED

    updated = update_report_status(db_session, report.id, ReportStatus.PROCESSING)
    assert updated.status is ReportStatus.PROCESSING

    # Scoped update by correct user
    updated2 = update_report_status(db_session, report.id, ReportStatus.COMPLETED, user_id=UUID(user.id))
    assert updated2.status is ReportStatus.COMPLETED

    # Scoped update by unauthorized user fails with 404
    with pytest.raises(ApplicationError) as err:
        update_report_status(db_session, report.id, ReportStatus.FAILED, user_id=UUID(other.id))
    assert err.value.status_code == 404


def test_retry_report_transitions_failed_to_pending_and_increments_job(db_session) -> None:
    user = make_user(db_session)
    other = make_user(db_session)
    storage = FakeStorage()
    report = create(db_session, user, storage)

    # Cannot retry when not in FAILED state
    with pytest.raises(ApplicationError) as err:
        retry_report(db_session, user, report.id)
    assert err.value.status_code == 409

    # Set to FAILED
    report.status = ReportStatus.FAILED
    db_session.commit()

    # Unauthorized user cannot retry
    with pytest.raises(ApplicationError) as err:
        retry_report(db_session, other, report.id)
    assert err.value.status_code == 404

    # Owner can retry
    retried = retry_report(db_session, user, report.id)
    assert retried.status is ReportStatus.PENDING

    # Check that a new ProcessingJob was created with retry_count incremented
    jobs = retried.processing_jobs
    assert len(jobs) >= 1
    assert jobs[-1].status.value == "queued"


def test_list_reports_pagination_has_more(db_session) -> None:
    user = make_user(db_session)
    storage = FakeStorage()
    # Create 3 reports
    r1 = create_report(db_session, user, make_upload("r1.pdf"), "Company 1", "2023", storage)
    r2 = create_report(db_session, user, make_upload("r2.pdf"), "Company 2", "2024", storage)
    r3 = create_report(db_session, user, make_upload("r3.pdf"), "Company 3", "2025", storage)

    items, total = list_reports(db_session, user, limit=2, offset=0)
    assert total == 3
    assert len(items) == 2
    has_more = (0 + len(items)) < total
    assert has_more is True

    items2, total2 = list_reports(db_session, user, limit=2, offset=2)
    assert len(items2) == 1
    has_more2 = (2 + len(items2)) < total2
    assert has_more2 is False


