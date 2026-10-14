from enum import StrEnum


class Role(StrEnum):
    CUSTOMER = "CUSTOMER"
    TESTER = "TESTER"
    DEVELOPER = "DEVELOPER"
    MANAGEMENT = "MANAGEMENT"
    ADMIN = "ADMIN"


class ConversationState(StrEnum):
    IDLE = "IDLE"
    AWAITING_PRODUCT = "AWAITING_PRODUCT"
    AWAITING_IMAGE = "AWAITING_IMAGE"
    AWAITING_DESCRIPTION = "AWAITING_DESCRIPTION"
    AWAITING_SEVERITY = "AWAITING_SEVERITY"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    SUBMITTING = "SUBMITTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BugStatus(StrEnum):
    PENDING_GITLAB = "pending_gitlab"
    OPENED = "opened"
    CLOSED = "closed"
    REOPENED = "reopened"
    FAILED = "failed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    DEAD = "dead"


ACTIVE_CONVERSATION_STATES = {
    ConversationState.AWAITING_PRODUCT,
    ConversationState.AWAITING_IMAGE,
    ConversationState.AWAITING_DESCRIPTION,
    ConversationState.AWAITING_SEVERITY,
    ConversationState.AWAITING_CONFIRMATION,
    ConversationState.SUBMITTING,
}

CUSTOMER_EVENTS = (
    "bug_submitted",
    "status_changed",
    "info_requested",
    "qa_result",
    "bug_closed",
    "bug_reopened",
)
DEVELOPER_EVENTS = (
    "issue_assigned",
    "issue_updated",
    "qa_feedback",
    "bug_reopened",
)
MANAGEMENT_EVENTS = (
    "bug_submitted",
    "bug_closed",
    "bug_reopened",
)

KNOWN_RULES = (
    [(event, "customer") for event in CUSTOMER_EVENTS]
    + [(event, "developer") for event in DEVELOPER_EVENTS]
    + [(event, "management") for event in MANAGEMENT_EVENTS]
)
