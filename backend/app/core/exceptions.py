"""Centralized exception response foundation."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import ApplicationError

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def application_exception_handler(
        request: Request, exc: ApplicationError
    ) -> JSONResponse:
        logger.info(
            "Application request rejected",
            extra={"path": request.url.path, "error_code": exc.code},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.public_message, "code": exc.code},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {"field": " → ".join(str(loc) for loc in err["loc"]), "message": err["msg"]}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Request validation failed.",
                "code": "validation_error",
                "errors": errors,
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled request error",
            extra={"path": request.url.path, "error_type": type(exc).__name__},
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "code": "internal_error"},
        )
