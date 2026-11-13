"""Errors as RFC 9457 problem details (application/problem+json)."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_BASE = "https://docs.farmulator.app/problems/"


class DomainError(Exception):
    """A rule of the farm business was broken. Carries what the client needs to fix it."""

    status = 400
    code = "bad_request"
    title = "Bad request"

    def __init__(self, detail: str, *, code: str | None = None, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        self.extra = extra


class NotFound(DomainError):
    status = 404
    code = "not_found"
    title = "Not found"


class Conflict(DomainError):
    status = 409
    code = "conflict"
    title = "Conflict"


class Invalid(DomainError):
    status = 422
    code = "invalid"
    title = "Invalid request"


class Unauthorized(DomainError):
    status = 401
    code = "unauthorized"
    title = "Sign-in required"


def problem(status: int, title: str, detail: str | None, code: str, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {
        "type": PROBLEM_BASE + code,
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        return problem(exc.status, exc.title, exc.detail, exc.code, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ()) if p != "body"),
                "message": err.get("msg", ""),
            }
            for err in exc.errors()
        ]
        return problem(
            422, "Invalid request", "Some fields need fixing.", "validation_failed", errors=errors
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return problem(exc.status_code, str(exc.detail), None, code)
