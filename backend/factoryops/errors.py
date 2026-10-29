"""Domain errors and the consistent JSON error envelope."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from factoryops.logging_setup import request_id_var

log = logging.getLogger(__name__)


class DomainError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, details: list[dict] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class InvalidTransition(DomainError):
    status_code = 409
    code = "invalid_transition"


class BadRequest(DomainError):
    status_code = 400
    code = "bad_request"


def _envelope(status: int, code: str, message: str, details: list[dict] | None = None):
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": request_id_var.get(),
            }
        },
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError):
        return _envelope(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [
            {
                "loc": [str(p) for p in e.get("loc", ())],
                "msg": str(e.get("msg")),
                "type": e.get("type"),
            }
            for e in exc.errors()
        ]
        return _envelope(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return _envelope(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError):
        log.warning("integrity error", extra={"error": str(exc.orig)})
        return _envelope(409, "conflict", "The change conflicts with existing data or constraints")

    @app.exception_handler(OperationalError)
    async def _db_unavailable(_: Request, exc: OperationalError):
        log.error("database unavailable", extra={"error": str(exc.orig)})
        return _envelope(503, "database_unavailable", "The database is temporarily unavailable")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return _envelope(500, "internal_error", "An unexpected error occurred")
