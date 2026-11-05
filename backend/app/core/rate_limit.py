"""Shared Redis-backed FastAPI rate-limit dependencies."""

from __future__ import annotations

import logging
from hashlib import sha256
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from fastapi_limiter.depends import RateLimiter

from app.core.errors import ApplicationError
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.service import AuthenticatedUser

logger = logging.getLogger(__name__)


async def _ip_identifier(request: Request) -> str:
    """Use the ASGI client address; do not trust arbitrary X-Forwarded-For values."""
    client_host = request.client.host if request.client else "unknown"
    route_path = getattr(request.scope.get("route"), "path", request.scope.get("path", ""))
    return f"ip:{client_host}:{route_path}"


async def _user_identifier(request: Request) -> str:
    user_id = getattr(request.state, "rate_limit_user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    route_path = getattr(request.scope.get("route"), "path", request.scope.get("path", ""))
    user_key = sha256(str(user_id).encode("utf-8")).hexdigest()
    return f"user:{user_key}:{route_path}"


_ip_limiter = RateLimiter(times=60, minutes=1, identifier=_ip_identifier)
_user_limiter = RateLimiter(times=60, minutes=1, identifier=_user_identifier)
_sensitive_user_limiter = RateLimiter(times=10, minutes=1, identifier=_user_identifier)


async def _enforce_limit(
    limiter: RateLimiter,
    request: Request,
    response: Response,
) -> None:
    try:
        await limiter(request, response)
    except HTTPException:
        raise
    except Exception as exc:
        # Fail closed: a Redis outage must not silently disable request limits.
        logger.error("Redis rate-limit check failed", extra={"error_type": type(exc).__name__})
        raise ApplicationError(
            503,
            "rate_limiter_unavailable",
            "Request protection is temporarily unavailable",
        ) from exc


async def limit_by_ip(request: Request, response: Response) -> None:
    await _enforce_limit(_ip_limiter, request, response)


async def limit_by_authenticated_user(
    request: Request,
    response: Response,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> None:
    request.state.rate_limit_user_id = user.id
    await _enforce_limit(_user_limiter, request, response)


async def limit_sensitive_action_by_authenticated_user(
    request: Request,
    response: Response,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> None:
    request.state.rate_limit_user_id = user.id
    await _enforce_limit(_sensitive_user_limiter, request, response)

