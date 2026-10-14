import re

_SENSITIVE_KEYS = {
    "authorization",
    "access_token",
    "password",
    "token",
    "private-token",
    "private_token",
    "app_secret",
    "api_token",
    "gitlab_access_token",
    "whatsapp_access_token",
    "whatsapp_app_secret",
    "gitlab_webhook_secret",
    "secret",
    "verify_token",
}

_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+")
_TOKEN_QUERY = re.compile(r"(?i)((?:token|access_token|password|secret)=)[^&\s]+")


def redact_text(value: str) -> str:
    value = _BEARER.sub(r"\1[REDACTED]", value)
    value = _TOKEN_QUERY.sub(r"\1[REDACTED]", value)
    return value


def redact(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
