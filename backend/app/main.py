"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_limiter import FastAPILimiter
from redis import asyncio as redis_async

from app.core.logging import configure_logging

configure_logging()

from app.api.routes import health, reports, users  # noqa: E402
from app.config import settings  # noqa: E402
from app.core.exceptions import register_exception_handlers  # noqa: E402
from app.core.middleware import RequestIdMiddleware, SecurityHeadersMiddleware  # noqa: E402
from app.database import check_database_connectivity  # noqa: E402

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect shared Redis limits and verify database before serving requests."""
    logger.info("Starting %s", settings.app_name)
    redis_client = redis_async.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )
    limiter_initialized = False
    try:
        await FastAPILimiter.init(redis_client, prefix="fininsight:rate-limit")
        limiter_initialized = True
        check_database_connectivity()
        logger.info("Startup checks passed — application ready.")
        yield
    finally:
        if limiter_initialized:
            await FastAPILimiter.close()
            FastAPILimiter.redis = None
            FastAPILimiter.lua_sha = None
            FastAPILimiter.prefix = None
        else:
            await redis_client.aclose()
        logger.info("Shutting down %s", settings.app_name)


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(RequestIdMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

register_exception_handlers(app)
app.include_router(health.router, prefix=settings.api_v1_prefix)
app.include_router(users.router, prefix=f"{settings.api_v1_prefix}/users")
app.include_router(reports.router, prefix=f"{settings.api_v1_prefix}/reports")
