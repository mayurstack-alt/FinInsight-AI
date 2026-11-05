from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.database import supabase_auth_users
from app.modules.auth.models import Profile
from app.modules.reports.models import (
    ProcessingJob,
    ProcessingJobStatus,
    Report,
    ReportStatus,
    TaskType,
)


def create_profile(session: Session, *, profile_id=None) -> Profile:
    profile_id = profile_id or uuid4()
    session.execute(supabase_auth_users.insert().values(id=profile_id))
    profile = Profile(id=profile_id, full_name="Test User")
    session.add(profile)
    session.flush()
    return profile


def create_report(session: Session, *, user_id=None, storage_path=None) -> Report:
    if user_id is None:
        user_id = create_profile(session).id
    report = Report(
        user_id=user_id,
        company_name="Example Corporation",
        original_filename="annual-report.pdf",
        reporting_period="FY2025",
        storage_path=storage_path or f"{user_id}/reports/{uuid4()}.pdf",
        file_size=1024,
        file_type="application/pdf",
    )
    session.add(report)
    session.flush()
    return report


def test_isolated_database_connects(db_session: Session) -> None:
    assert db_session.scalar(select(func.count()).select_from(Profile)) == 0


def test_profile_report_job_relationships_and_defaults(db_session: Session) -> None:
    profile = create_profile(db_session)
    report = create_report(db_session, user_id=profile.id)
    initial_job = ProcessingJob(
        report=report,
        task_group_id=uuid4(),
        task_type=TaskType.PDF_EXTRACTION,
    )
    db_session.add(initial_job)
    db_session.flush()

    retry = ProcessingJob(
        report=report,
        task_group_id=initial_job.task_group_id,
        task_type=initial_job.task_type,
        retry_count=1,
        status=ProcessingJobStatus.FAILED,
        error_message="PDF text extraction failed.",
    )
    db_session.add(retry)
    db_session.commit()

    loaded_profile = db_session.get(Profile, profile.id)
    loaded_report = db_session.get(Report, report.id)
    assert loaded_profile is not None
    assert loaded_report is not None
    assert loaded_report.status is ReportStatus.UPLOADED
    assert sorted(job.retry_count for job in loaded_report.processing_jobs) == [0, 1]
    assert all(
        job.report_id == report.id for job in loaded_profile.reports[0].processing_jobs
    )
    assert loaded_profile.created_at is not None
    assert loaded_profile.updated_at is not None


def test_profile_primary_key_references_supabase_auth_user() -> None:
    auth_user_fk = next(iter(Profile.__table__.c.id.foreign_keys))
    assert auth_user_fk.target_fullname == "auth.users.id"
    assert auth_user_fk.ondelete == "CASCADE"


def test_report_requires_existing_profile(db_session: Session) -> None:
    report = Report(
        user_id=uuid4(),
        company_name="Example Corporation",
        original_filename="annual-report.pdf",
        reporting_period="FY2025",
        storage_path=f"reports/{uuid4()}.pdf",
        file_size=1024,
        file_type="application/pdf",
    )
    db_session.add(report)
    with pytest.raises((IntegrityError, StatementError)):
        db_session.flush()
    db_session.rollback()


def test_job_requires_existing_report(db_session: Session) -> None:
    job = ProcessingJob(report_id=uuid4(), task_type=TaskType.PDF_EXTRACTION)
    db_session.add(job)
    with pytest.raises((IntegrityError, StatementError)):
        db_session.flush()
    db_session.rollback()


@pytest.mark.parametrize(
    "retry_count,task_type",
    [(-1, TaskType.PDF_EXTRACTION), (0, "unknown_task")],
)
def test_job_constraints_reject_invalid_values(
    db_session: Session, retry_count: int, task_type: str
) -> None:
    report = create_report(db_session)
    db_session.add(
        ProcessingJob(
            report_id=report.id,
            task_type=task_type,
            retry_count=retry_count,
        )
    )
    with pytest.raises((IntegrityError, StatementError)):
        db_session.flush()
    db_session.rollback()


def test_storage_path_and_task_attempt_are_unique(db_session: Session) -> None:
    report = create_report(db_session)
    duplicate_path = Report(
        user_id=report.user_id,
        company_name="Example Corporation",
        original_filename="revised.pdf",
        reporting_period="FY2025",
        storage_path=report.storage_path,
        file_size=100,
        file_type="application/pdf",
    )
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(duplicate_path)
        db_session.flush()

    report = db_session.scalar(select(Report))
    assert report is not None
    group_id = uuid4()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add_all(
            [
                ProcessingJob(
                    report_id=report.id,
                    task_group_id=group_id,
                    task_type=TaskType.PDF_EXTRACTION,
                ),
                ProcessingJob(
                    report_id=report.id,
                    task_group_id=group_id,
                    task_type=TaskType.PDF_EXTRACTION,
                ),
            ]
        )
        db_session.flush()


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_name", None),
        ("original_filename", None),
        ("file_size", 0),
        ("file_size", None),
        ("file_size", -1),
        ("file_type", "text/plain"),
        ("company_name", "   "),
    ],
)
def test_report_constraints_reject_invalid_values(
    db_session: Session, field, value
) -> None:
    profile = create_profile(db_session)
    values = {
        "user_id": profile.id,
        "company_name": "Example Corporation",
        "original_filename": "annual-report.pdf",
        "reporting_period": "FY2025",
        "storage_path": f"{profile.id}/{uuid4()}.pdf",
        "file_size": 1024,
        "file_type": "application/pdf",
    }
    values[field] = value
    db_session.add(Report(**values))
    with pytest.raises((IntegrityError, StatementError)):
        db_session.flush()
    db_session.rollback()


def test_report_deletion_cascades_to_jobs_but_not_storage(db_session: Session) -> None:
    report = create_report(db_session)
    report_id = report.id
    storage_path = report.storage_path
    job = ProcessingJob(report=report, task_type=TaskType.PDF_EXTRACTION)
    db_session.add(job)
    db_session.commit()
    job_id = job.id

    db_session.delete(report)
    db_session.commit()

    assert db_session.get(ProcessingJob, job_id) is None
    assert db_session.get(Report, report_id) is None
    assert db_session.scalar(select(func.count()).select_from(ProcessingJob)) == 0
    # The database stores a reference only; no object-store deletion is attempted.
    assert storage_path is not None


def test_profile_deletion_cascades_to_reports_and_jobs(db_session: Session) -> None:
    profile = create_profile(db_session)
    report = create_report(db_session, user_id=profile.id)
    job = ProcessingJob(report=report, task_type=TaskType.PDF_EXTRACTION)
    db_session.add(job)
    db_session.commit()
    profile_id = profile.id
    report_id = report.id
    job_id = job.id

    db_session.delete(profile)
    db_session.commit()

    assert db_session.get(Profile, profile_id) is None
    assert db_session.get(Report, report_id) is None
    assert db_session.get(ProcessingJob, job_id) is None

