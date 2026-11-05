"""Application profile data linked to Supabase Auth identities."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.modules.reports.models import Report


class Profile(Base):
    """Application-owned profile fields for one Supabase Auth user."""

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint(
            "full_name IS NULL OR length(trim(full_name)) BETWEEN 1 AND 200",
            name="full_name_valid",
        ),
        CheckConstraint(
            "avatar_url IS NULL OR length(avatar_url) <= 2048",
            name="avatar_url_length",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    reports: Mapped[list[Report]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
