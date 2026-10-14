import hashlib
import hmac
import re


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def safe_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def webhook_secret_match(provided: str | None, expected: str) -> bool:
    if not provided or not expected:
        return False
    return safe_compare(provided, expected)


def verify_meta_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    provided = header.removeprefix("sha256=")
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def normalize_phone(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) < 8 or len(digits) > 15:
        raise ValueError("Phone number must contain 8 to 15 digits")
    return f"+{digits}"


def phone_digits(value: str | None) -> str:
    if not value:
        return ""
    return "".join(character for character in value if character.isdigit())


_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def require_slug(value: str) -> str:
    if not _SLUG.fullmatch(value):
        raise ValueError("Slug must use lowercase letters, numbers, and hyphens")
    return value
