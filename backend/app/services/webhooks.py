import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import UnauthorizedError, ValidationAppError
from app.core.redaction import redact
from app.core.security import verify_meta_signature, webhook_secret_match
from app.domain.enums import BugStatus
from app.domain.gitlab_events import labels_added, map_gitlab_status, notification_events_for_gitlab, parse_gitlab_issue_event
from app.domain.models import BugReport, NotificationOutbox, WebhookEvent
from app.domain.whatsapp_events import parse_whatsapp_payload
from app.services.audit import add_audit
from app.services.backoff import exponential_backoff_seconds
from app.services.conversations import ConversationService
from app.services.notifications import NotificationService

MAX_WEBHOOK_BYTES = 1_000_000


class WebhookService:
    def __init__(self, session: Session, settings: Settings, conversations: ConversationService):
        self.session = session
        self.settings = settings
        self.conversations = conversations
        self.notifications = NotificationService(session)

    def verify_whatsapp(self, mode: str | None, token: str | None, challenge: str | None) -> str:
        expected = self.settings.whatsapp_verify_token
        if mode != "subscribe" or not challenge or not expected or not token:
            raise UnauthorizedError("WhatsApp verification failed")
        if not verify_token(token, expected):
            raise UnauthorizedError("WhatsApp verification failed")
        return challenge

    def handle_whatsapp(self, body: bytes, signature: str | None) -> dict:
        self._limit(body)
        if not self.settings.whatsapp_app_secret or not verify_meta_signature(self.settings.whatsapp_app_secret, body, signature):
            raise UnauthorizedError("Invalid WhatsApp signature")
        payload = self._json(body)
        external_id = hashlib.sha256(body).hexdigest()
        event, duplicate = self._begin_event("whatsapp", external_id, payload)
        if duplicate:
            return {"status": "duplicate"}
        messages, statuses = parse_whatsapp_payload(payload)
        try:
            for status in statuses:
                self._apply_delivery_status(status.message_id, status.status)
            results = [self.conversations.handle_inbound(message, external_id) for message in messages]
            outcome = "duplicate" if results and all(result == "duplicate" for result in results) else "ok"
            event = self._reload(event.id)
            event.status = "processed"
            event.processed_at = datetime.now(timezone.utc)
            self.session.commit()
            return {"status": outcome}
        except Exception as exc:
            self._mark_failed(event.id, exc)
            raise

    def handle_gitlab(self, body: bytes, token: str | None, event_uuid: str | None) -> dict:
        self._limit(body)
        if not webhook_secret_match(token, self.settings.gitlab_webhook_secret):
            raise UnauthorizedError("Invalid GitLab webhook token")
        payload = self._json(body)
        external_id = event_uuid or hashlib.sha256(body).hexdigest()
        event, duplicate = self._begin_event("gitlab", external_id, payload)
        if duplicate:
            return {"status": "duplicate"}
        parsed = parse_gitlab_issue_event(payload)
        if parsed is None:
            event = self._reload(event.id)
            event.status = "ignored"
            event.processed_at = datetime.now(timezone.utc)
            self.session.commit()
            return {"status": "ignored"}
        bug = self.session.scalar(
            select(BugReport).where(
                BugReport.gitlab_project_id == parsed.project_id,
                BugReport.gitlab_issue_iid == parsed.issue_iid,
            )
        )
        if bug is None:
            event = self._reload(event.id)
            event.status = "ignored"
            event.processed_at = datetime.now(timezone.utc)
            self.session.commit()
            return {"status": "ignored"}
        previous = bug.status
        bug.status = map_gitlab_status(action=parsed.action, state=parsed.state, current_status=previous)
        if parsed.url:
            bug.gitlab_issue_url = parsed.url
        add_audit(
            self.session,
            action="gitlab_state_changed",
            entity_type="bug_report",
            entity_id=str(bug.id),
            detail={"from": previous, "to": bug.status, "action": parsed.action, "audience": "customer"},
        )
        events = notification_events_for_gitlab(parsed.action, bug.status, labels_added(payload))
        self.notifications.enqueue_gitlab_events(bug, events, external_id)
        event = self._reload(event.id)
        event.status = "processed"
        event.processed_at = datetime.now(timezone.utc)
        self.session.commit()
        return {"status": "ok", "bug_status": bug.status if bug.status else BugStatus.OPENED}

    def _apply_delivery_status(self, message_id: str, status: str) -> None:
        row = self.session.scalar(select(NotificationOutbox).where(NotificationOutbox.provider_message_id == message_id))
        if row is None:
            return
        row.delivery_status = status
        if status != "failed":
            return
        row.attempts += 1
        row.last_error = "delivery status failed"
        if row.attempts >= self.settings.notification_max_attempts:
            row.status = "dead"
            return
        row.status = "pending"
        row.provider_message_id = None
        delay = exponential_backoff_seconds(
            row.attempts,
            self.settings.notification_backoff_base_seconds,
            self.settings.notification_backoff_cap_seconds,
        )
        row.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)

    def _begin_event(self, provider: str, external_id: str, payload: dict) -> tuple[WebhookEvent, bool]:
        existing = self.session.scalar(
            select(WebhookEvent).where(WebhookEvent.provider == provider, WebhookEvent.external_id == external_id)
        )
        if existing and existing.status in {"processed", "ignored"}:
            return existing, True
        if existing and existing.status == "failed":
            existing.status = "processing"
            existing.error = None
            self.session.commit()
            return existing, False
        if existing:
            return existing, False
        event = WebhookEvent(
            provider=provider,
            external_id=external_id,
            payload=redact(payload),
            status="processing",
        )
        try:
            with self.session.begin_nested():
                self.session.add(event)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(
                select(WebhookEvent).where(WebhookEvent.provider == provider, WebhookEvent.external_id == external_id)
            )
            if existing is None:
                raise
            return existing, existing.status in {"processed", "ignored"}
        self.session.commit()
        return event, False

    def _reload(self, event_id) -> WebhookEvent:
        event = self.session.get(WebhookEvent, event_id)
        if event is None:
            raise ValidationAppError("Webhook event disappeared")
        return event

    def _mark_failed(self, event_id, exc: Exception) -> None:
        self.session.rollback()
        event = self.session.get(WebhookEvent, event_id)
        if event is None:
            return
        event.status = "failed"
        event.error = type(exc).__name__
        self.session.commit()

    def _json(self, body: bytes) -> dict:
        try:
            payload = json.loads(body.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationAppError("Invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValidationAppError("Invalid JSON")
        return payload

    def _limit(self, body: bytes) -> None:
        if len(body) > MAX_WEBHOOK_BYTES:
            raise ValidationAppError("Payload too large")


def verify_token(provided: str, expected: str) -> bool:
    from app.core.security import safe_compare

    return safe_compare(provided, expected)
