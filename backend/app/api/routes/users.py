"""Authenticated user profile routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.core.rate_limit import (
    limit_by_authenticated_user,
    limit_by_ip,
    limit_sensitive_action_by_authenticated_user,
)
from app.modules.auth.models import Profile
from app.modules.auth.service import AuthenticatedUser
from app.schemas.auth import UserProfileResponse, UserProfileUpdateRequest

router = APIRouter(dependencies=[Depends(limit_by_ip), Depends(limit_by_authenticated_user)])


@router.get("/me", response_model=UserProfileResponse)
def get_my_profile(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserProfileResponse:
    user_uuid = UUID(user.id)
    profile = db.get(Profile, user_uuid)
    return UserProfileResponse(
        id=user.id,
        email=user.email,
        full_name=profile.full_name if profile else None,
        avatar_url=profile.avatar_url if profile else None,
    )


@router.patch(
    "/me",
    response_model=UserProfileResponse,
    dependencies=[Depends(limit_sensitive_action_by_authenticated_user)],
)
def update_my_profile(
    payload: UserProfileUpdateRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserProfileResponse:
    user_uuid = UUID(user.id)
    profile = db.get(Profile, user_uuid)
    if profile is None:
        profile = Profile(id=user_uuid)
        db.add(profile)

    if payload.full_name is not None:
        profile.full_name = payload.full_name.strip() or None
    if payload.avatar_url is not None:
        profile.avatar_url = payload.avatar_url.strip() or None

    db.commit()
    db.refresh(profile)

    return UserProfileResponse(
        id=user.id,
        email=user.email,
        full_name=profile.full_name,
        avatar_url=profile.avatar_url,
    )
