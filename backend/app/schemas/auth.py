"""Public authentication-related API schemas."""

from pydantic import BaseModel, ConfigDict, Field


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    email: str | None = None
    full_name: str | None = None
    avatar_url: str | None = None


class UserProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    avatar_url: str | None = Field(default=None, max_length=2048)
