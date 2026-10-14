from sqlalchemy.orm import Session

from app.core.logging import correlation_id_var
from app.core.redaction import redact
from app.domain.models import AuditLog


def add_audit(
    session: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None,
    detail: dict | None = None,
    actor_user_id=None,
    correlation_id: str | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=redact(detail or {}),
            correlation_id=correlation_id or correlation_id_var.get(),
        )
    )
