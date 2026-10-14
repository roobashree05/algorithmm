import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.core.errors import IntegrationError
from app.core.redaction import redact_text
from app.core.security import phone_digits
from app.domain.models import BugReport, NotificationOutbox, Product, User
from app.domain.permissions import delivery_allowed
from app.repositories.access import linked_to_customer, on_team
from app.services.attachments import AttachmentService, AttachmentStore
from app.services.audit import add_audit
from app.services.backoff import exponential_backoff_seconds

logger = logging.getLogger(__name__)


def process_outbox_batch(session_factory, whatsapp, settings, now: datetime | None = None) -> int:
    moment = now or datetime.now(timezone.utc)
    session = session_factory()
    delivered = 0
    try:
        stale_before = moment - timedelta(minutes=2)
        session.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.status == "sending", NotificationOutbox.updated_at < stale_before)
            .values(status="pending")
        )
        session.commit()
        rows = list(
            session.scalars(
                select(NotificationOutbox)
                .where(NotificationOutbox.status == "pending", NotificationOutbox.next_attempt_at <= moment)
                .order_by(NotificationOutbox.next_attempt_at)
                .limit(50)
                .with_for_update(skip_locked=True)
            ).all()
        )
        for row in rows:
            row.status = "sending"
            row.updated_at = moment
        session.commit()
        for row in rows:
            if _deliver(session, row, whatsapp, settings, moment):
                delivered += 1
        _cleanup(session, settings, moment)
        session.commit()
        return delivered
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _deliver(session, row: NotificationOutbox, whatsapp, settings, now: datetime) -> bool:
    payload = dict(row.payload or {})
    destination = str(payload.get("to") or "")
    if "@g.us" in destination:
        return _dead(session, row, "group_destination_not_supported")
    user = session.get(User, row.recipient_user_id) if row.recipient_user_id else None
    if row.audience == "conversation":
        if user is not None and (not user.is_active or phone_digits(user.phone_e164) != destination):
            return _dead(session, row, "recipient_not_authorized")
        if not destination:
            return _dead(session, row, "no_phone")
    else:
        bug = session.get(BugReport, row.bug_report_id) if row.bug_report_id else None
        product = session.get(Product, bug.product_id) if bug is not None else None
        allowed = False
        if user is not None and bug is not None and product is not None:
            allowed = delivery_allowed(
                audience=row.audience,
                recipient_role=user.role,
                recipient_active=user.is_active,
                is_reporter=user.id == bug.reporter_user_id,
                linked_to_customer=linked_to_customer(session, user.id, bug.customer_id),
                on_team=on_team(session, user.id, product.developer_team_id),
            )
        if not allowed or not user or phone_digits(user.phone_e164) != destination or not destination:
            return _dead(session, row, "recipient_not_authorized" if destination else "no_phone")
    try:
        message_id = _send(whatsapp, destination, payload)
    except IntegrationError as exc:
        row.attempts += 1
        row.last_error = redact_text(str(exc))[:500]
        if row.attempts >= settings.notification_max_attempts:
            row.status = "dead"
        else:
            row.status = "pending"
            delay = exponential_backoff_seconds(
                row.attempts,
                settings.notification_backoff_base_seconds,
                settings.notification_backoff_cap_seconds,
            )
            row.next_attempt_at = now + timedelta(seconds=delay)
        add_audit(
            session,
            action="notification_failed",
            entity_type="bug_report" if row.bug_report_id else "conversation",
            entity_id=str(row.bug_report_id or row.conversation_id or ""),
            detail={"audience": row.audience, "event_type": row.event_type, "attempts": row.attempts},
        )
        return False
    row.status = "sent"
    row.provider_message_id = message_id
    row.delivery_status = "sent"
    row.last_error = None
    add_audit(
        session,
        action="notification_sent",
        entity_type="bug_report" if row.bug_report_id else "conversation",
        entity_id=str(row.bug_report_id or row.conversation_id or ""),
        actor_user_id=row.recipient_user_id,
        detail={"audience": row.audience, "event_type": row.event_type},
    )
    return True


def _dead(session, row: NotificationOutbox, reason: str) -> bool:
    row.status = "dead"
    row.last_error = reason
    add_audit(
        session,
        action="notification_suppressed",
        entity_type="bug_report" if row.bug_report_id else "conversation",
        entity_id=str(row.bug_report_id or row.conversation_id or ""),
        detail={"audience": row.audience, "event_type": row.event_type, "reason": reason},
    )
    return False


def _send(whatsapp, destination: str, payload: dict) -> str:
    kind = payload.get("kind") or "text"
    body = str(payload.get("body") or "")
    options = payload.get("options") or []
    if kind == "list":
        return whatsapp.send_list(destination, body, options)
    if kind == "buttons":
        return whatsapp.send_buttons(destination, body, options)
    return whatsapp.send_text(destination, body)


def _cleanup(session, settings, now: datetime) -> None:
    try:
        service = AttachmentService(session, AttachmentStore(settings.attachment_dir), settings.attachment_max_bytes)
        service.cleanup_temporary(now - timedelta(hours=settings.attachment_retention_hours))
    except Exception:
        logger.exception("temporary attachment cleanup failed")
