"""
Application exception hierarchy and FastAPI exception handlers.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base class for all application errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "internal_error"

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    error_code = "conflict"


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "validation_error"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "invalid_api_key"


class AuthorizationError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    error_code = "insufficient_scope"


class FileTooLargeError(AppError):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    error_code = "file_too_large"


class UnsupportedFileTypeError(AppError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    error_code = "unsupported_file_type"


class ProcessingError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "processing_error"


class ApiDeprecatedError(AppError):
    status_code = status.HTTP_410_GONE
    error_code = "api_deprecated"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    error_code = "rate_limit_exceeded"


class QuotaExceededError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    error_code = "quota_exceeded"


# ── FastAPI handler ───────────────────────────────────────────────────────────

def _error_body(exc: AppError) -> dict:
    return {
        "error": {
            "code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_error_body(exc))

    @app.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_error_body(exc))
