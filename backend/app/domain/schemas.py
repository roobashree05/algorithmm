import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import require_slug
from app.domain.enums import Role, Severity

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ErrorBody(BaseModel):
    code: str
    message: str
    correlation_id: str
    details: list | dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class BugCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_slug: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=3, max_length=5000)
    severity: Severity
    customer_code: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=200)

    @field_validator("product_slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        return require_slug(value)


class AttachmentRead(BaseModel):
    id: UUID
    content_type: str
    size_bytes: int
    original_filename: str


class BugRead(BaseModel):
    id: UUID
    product_slug: str
    product_name: str
    customer_code: str
    severity: str
    status: str
    title: str
    description: str
    source: str
    gitlab_issue_iid: str | None
    gitlab_issue_url: str | None
    gitlab_project_id: str | None = None
    attachments: list[AttachmentRead]
    created_at: datetime
    updated_at: datetime


class BugList(BaseModel):
    items: list[BugRead]
    total: int


class HistoryEvent(BaseModel):
    action: str
    at: datetime
    detail: dict


class BugHistory(BaseModel):
    bug_id: UUID
    events: list[HistoryEvent]


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=5000)
    visibility: str = "internal"

    @field_validator("visibility")
    @classmethod
    def _visibility(cls, value: str) -> str:
        if value not in {"internal", "customer_request"}:
            raise ValueError("visibility must be internal or customer_request")
        return value


class QaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: str
    comment: str = Field(min_length=1, max_length=5000)

    @field_validator("result")
    @classmethod
    def _result(cls, value: str) -> str:
        if value not in {"passed", "failed"}:
            raise ValueError("result must be passed or failed")
        return value


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=64)
    gitlab_project_id: str = Field(min_length=1, max_length=255)
    gitlab_project_path: str = Field(min_length=1, max_length=255)
    developer_team_slug: str = Field(min_length=1, max_length=64)
    default_labels: list[str] = Field(default_factory=list)
    default_assignee_gitlab_id: str | None = None
    is_active: bool = True

    @field_validator("slug", "developer_team_slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        return require_slug(value)

    @field_validator("default_labels")
    @classmethod
    def _labels(cls, value: list[str]) -> list[str]:
        cleaned = []
        for label in value:
            item = label.strip()
            if not item or len(item) > 64:
                raise ValueError("Labels must be 1 to 64 characters")
            cleaned.append(item)
        return cleaned


class ProductRead(BaseModel):
    id: UUID
    name: str
    slug: str
    gitlab_project_id: str
    gitlab_project_path: str
    developer_team_slug: str
    default_labels: list[str]
    default_assignee_gitlab_id: str | None
    is_active: bool


class TeamCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=64)
    member_emails: list[str] = Field(default_factory=list)

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        return require_slug(value)

    @field_validator("member_emails")
    @classmethod
    def _emails(cls, value: list[str]) -> list[str]:
        emails = []
        for item in value:
            email = item.strip().lower()
            if not _EMAIL.fullmatch(email):
                raise ValueError(f"Invalid email: {item}")
            emails.append(email)
        return emails


class TeamRead(BaseModel):
    id: UUID
    name: str
    slug: str
    member_emails: list[str]


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=64)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return require_slug(value)


class CustomerRead(BaseModel):
    id: UUID
    name: str
    code: str
    is_active: bool


class UserMappingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    display_name: str = Field(min_length=1, max_length=200)
    role: Role
    phone_e164: str | None = None
    customer_code: str | None = None
    team_slug: str | None = None
    api_token: str | None = Field(default=None, min_length=12, max_length=200)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        email = value.strip().lower()
        if not _EMAIL.fullmatch(email):
            raise ValueError("Invalid email")
        return email


class UserMappingRead(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    role: str
    phone_e164: str | None
    customer_code: str | None
    team_slug: str | None
    api_token: str | None = None


class NotificationRuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(min_length=1, max_length=64)
    audience: str
    product_slug: str | None = None
    enabled: bool = True

    @field_validator("audience")
    @classmethod
    def _audience(cls, value: str) -> str:
        if value not in {"customer", "developer", "management"}:
            raise ValueError("audience must be customer, developer, or management")
        return value


class NotificationRuleRead(BaseModel):
    id: UUID
    event_type: str
    audience: str
    product_slug: str | None
    enabled: bool


class ProductSummary(BaseModel):
    slug: str
    name: str
    opened: int
    closed: int
    reopened: int
    pending: int


class TeamSummary(BaseModel):
    slug: str
    name: str
    opened: int
    closed: int
    reopened: int


class SummaryResponse(BaseModel):
    products: list[ProductSummary] | None = None
    teams: list[TeamSummary] | None = None
