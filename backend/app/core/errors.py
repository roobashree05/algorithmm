class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__("unauthorized", message, 401)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Forbidden"):
        super().__init__("forbidden", message, 403)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found"):
        super().__init__("not_found", message, 404)


class ValidationAppError(AppError):
    def __init__(self, message: str):
        super().__init__("validation_error", message, 422)


class ConflictError(AppError):
    def __init__(self, message: str):
        super().__init__("conflict", message, 409)


class RateLimitError(AppError):
    def __init__(self, message: str = "Too many requests"):
        super().__init__("rate_limited", message, 429)


class IntegrationError(Exception):
    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
