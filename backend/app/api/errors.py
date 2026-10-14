import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import AppError
from app.core.logging import correlation_id_var

logger = logging.getLogger(__name__)


def error_body(code: str, message: str, details=None) -> dict:
    body = {"code": code, "message": message, "correlation_id": correlation_id_var.get()}
    if details is not None:
        body["details"] = details
    return {"error": body}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError):
        return _json(exc.status_code, error_body(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = [{"loc": list(item.get("loc", [])), "msg": item.get("msg", "")} for item in exc.errors()]
        return _json(422, error_body("validation_error", "Invalid request", details))

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        logger.exception("unhandled error")
        return _json(500, error_body("internal_error", "Internal server error"))


def _json(status_code: int, content: dict) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["X-Correlation-ID"] = correlation_id_var.get()
    return response
