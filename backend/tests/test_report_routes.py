from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.api.routes import reports
from app.database import supabase_auth_users
from app.main import app
from app.modules.auth.models import Profile
from app.modules.auth.service import AuthenticatedUser
from app.modules.reports import service as report_service
from app.modules.reports.models import Report, ReportStatus
from tests.pdf_samples import minimal_pdf


class FakeRouteStorage:
    def __init__(self) -> None:
        self.objects: set[str] = set()

    def upload(self, path: str, stream) -> None:
        self.objects.add(path)

    def delete(self, path: str) -> None:
        self.objects.discard(path)

    def exists(self, path: str) -> bool:
        return path in self.objects

    def create_signed_download_url(self, path: str):
        return "https://storage.example/signed-url", 300


def test_all_report_routes_are_registered_under_versioned_prefix() -> None:
    paths = set(app.openapi()["paths"])
    assert {
        "/api/v1/reports",
        "/api/v1/reports/{report_id}",
        "/api/v1/reports/{report_id}/status",
        "/api/v1/reports/{report_id}/download",
        "/api/v1/reports/{report_id}/retry",
    }.issubset(paths)
    assert reports.router.routes


def test_missing_bearer_token_is_unauthorized_for_report_dependency() -> None:
    with pytest.raises(HTTPException) as error:
        get_current_user(None).send(None)
    assert error.value.status_code == 401


def test_create_and_get_and_delete_report_http_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import StaticPool
    from app.database import Base

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"auth": None}},
    )

    @event.listens_for(test_engine, "connect")
    def enable_sqlite_fk(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(test_engine)

    fake_storage = FakeRouteStorage()
    monkeypatch.setattr(report_service, "storage", fake_storage)

    user_id = uuid4()
    with Session(test_engine) as init_session:
        init_session.execute(supabase_auth_users.insert().values(id=user_id))
        init_session.commit()

    test_user = AuthenticatedUser(id=str(user_id), email="route_test@example.com")

    def override_get_db():
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: test_user
    client = TestClient(app)

    try:
        # 1. Create report via multipart POST
        pdf_bytes = minimal_pdf()
        response = client.post(
            "/api/v1/reports",
            data={"company_name": "Acme Corp", "reporting_period": "FY2025"},
            files={"file": ("acme_2025.pdf", BytesIO(pdf_bytes), "application/pdf")},
        )
        assert response.status_code == 201
        report_data = response.json()
        report_id = report_data["id"]
        assert report_data["company_name"] == "Acme Corp"
        assert report_data["status"] == "uploaded"

        # 2. Get report by ID
        get_res = client.get(f"/api/v1/reports/{report_id}")
        assert get_res.status_code == 200
        assert get_res.json()["id"] == report_id

        # 3. List reports
        list_res = client.get("/api/v1/reports?limit=10&offset=0")
        assert list_res.status_code == 200
        list_data = list_res.json()
        assert list_data["total"] == 1
        assert len(list_data["items"]) == 1
        assert list_data["has_more"] is False

        # 4. Check status route
        status_res = client.get(f"/api/v1/reports/{report_id}/status")
        assert status_res.status_code == 200
        assert status_res.json()["status"] == "uploaded"

        # 5. Check download signed URL
        dl_res = client.get(f"/api/v1/reports/{report_id}/download")
        assert dl_res.status_code == 200
        assert dl_res.json()["signed_url"] == "https://storage.example/signed-url"
        assert dl_res.json()["expires_in_seconds"] == 300

        # 6. Retry report should fail if not in FAILED state (409 Conflict)
        retry_res = client.post(f"/api/v1/reports/{report_id}/retry")
        assert retry_res.status_code == 409

        # Mark as failed directly in DB to test successful retry
        from uuid import UUID
        with Session(test_engine) as session:
            report_obj = session.get(Report, UUID(report_id))
            report_obj.status = ReportStatus.FAILED
            session.commit()

        # Retry should now succeed (200 OK)
        retry_res = client.post(f"/api/v1/reports/{report_id}/retry")
        assert retry_res.status_code == 200
        assert retry_res.json()["status"] == "pending"

        # 7. Delete report (204 No Content)
        del_res = client.delete(f"/api/v1/reports/{report_id}")
        assert del_res.status_code == 204

        # Subsequent GET should return 404
        get_after_del = client.get(f"/api/v1/reports/{report_id}")
        assert get_after_del.status_code == 404
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(test_engine)
        test_engine.dispose()

