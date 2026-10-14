from collections.abc import Generator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, UnauthorizedError
from app.domain.enums import Role
from app.domain.models import User
from app.services.admin import AdminService
from app.services.attachments import AttachmentService, AttachmentStore
from app.services.bugs import BugService
from app.services.conversations import ConversationService
from app.services.identity import IdentityService
from app.services.reporting import ReportingService
from app.services.webhooks import WebhookService

bearer_scheme = HTTPBearer(auto_error=False)


def get_db(request: Request) -> Generator[Session, None, None]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise UnauthorizedError()
    return IdentityService(db).user_from_token(credentials.credentials)


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.ADMIN:
        raise ForbiddenError("Admin role is required")
    return user


def require_summary_reader(user: User = Depends(get_current_user)) -> User:
    if user.role not in {Role.MANAGEMENT, Role.ADMIN}:
        raise ForbiddenError("Management role is required")
    return user


def get_bug_service(request: Request, db: Session = Depends(get_db)) -> BugService:
    settings = request.app.state.settings
    attachments = AttachmentService(db, AttachmentStore(settings.attachment_dir), settings.attachment_max_bytes)
    reporting = ReportingService(db, request.app.state.gitlab, settings, attachments)
    return BugService(db, reporting)


def get_admin_service(db: Session = Depends(get_db)) -> AdminService:
    return AdminService(db)


def get_webhook_service(request: Request, db: Session = Depends(get_db)) -> WebhookService:
    settings = request.app.state.settings
    attachments = AttachmentService(db, AttachmentStore(settings.attachment_dir), settings.attachment_max_bytes)
    reporting = ReportingService(db, request.app.state.gitlab, settings, attachments)
    conversations = ConversationService(db, settings, request.app.state.whatsapp, reporting, attachments)
    return WebhookService(db, settings, conversations)
