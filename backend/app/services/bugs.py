import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, IntegrationError, NotFoundError, AppError
from app.domain.enums import Role
from app.domain.models import AuditLog, BugAttachment, BugReport, Customer, Product, User
from app.domain.permissions import can_view_bug
from app.domain.schemas import AttachmentRead, BugHistory, BugList, BugRead, HistoryEvent, ProductSummary, TeamSummary
from app.repositories.access import linked_to_customer, on_team, visible_bug_statement
from app.services.audit import add_audit
from app.services.identity import IdentityService
from app.services.notifications import NotificationService
from app.services.reporting import ReportingService

_CUSTOMER_ROLES = {Role.CUSTOMER, Role.TESTER}
_NOTE_ROLES = {Role.DEVELOPER, Role.MANAGEMENT, Role.ADMIN}
_QA_ROLES = {Role.TESTER, Role.ADMIN}


class BugService:
    def __init__(self, session: Session, reporting: ReportingService):
        self.session = session
        self.reporting = reporting
        self.notifications = NotificationService(session)

    def create(
        self,
        *,
        reporter: User,
        product_slug: str,
        description: str,
        severity: str,
        customer_code: str | None,
        idempotency_key: str | None,
        attachment: BugAttachment | None,
        correlation_id: str | None,
    ) -> BugReport:
        identity = IdentityService(self.session)
        customer = identity.resolve_customer(reporter, customer_code)
        product = identity.resolve_product(product_slug)
        try:
            return self._submit(
            reporter=reporter,
            customer=customer,
            product=product,
            description=description,
            severity=severity,
            source="api",
            idempotency_key=idempotency_key,
            attachment=attachment,
            correlation_id=correlation_id,
            conversation_id=None,
            )
        except IntegrationError as exc:
            raise AppError("gitlab_unavailable", "GitLab could not create the issue. Retry with the same idempotency key.", 502) from exc

    def _submit(self, **kwargs):
        return self.reporting.submit(**kwargs)

    def list_bugs(self, user: User, *, status: str | None, product_slug: str | None, limit: int, offset: int) -> BugList:
        statement = visible_bug_statement(user)
        if status:
            statement = statement.where(BugReport.status == status)
        if product_slug:
            product_ids = select(Product.id).where(Product.slug == product_slug)
            statement = statement.where(BugReport.product_id.in_(product_ids))
        total = self.session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
        rows = self.session.scalars(statement.order_by(BugReport.created_at.desc()).limit(limit).offset(offset)).all()
        return BugList(items=[self.serialize(user, bug) for bug in rows], total=int(total))

    def get_bug(self, user: User, bug_id: uuid.UUID) -> BugReport:
        bug = self.session.get(BugReport, bug_id)
        if bug is None or not self._allowed(user, bug):
            if bug is not None:
                add_audit(
                    self.session,
                    action="unauthorized_access",
                    entity_type="bug_report",
                    entity_id=str(bug.id),
                    actor_user_id=user.id,
                    detail={"reason": "cross_customer_or_role"},
                )
                self.session.commit()
            raise NotFoundError("Bug report not found")
        return bug

    def history(self, user: User, bug_id: uuid.UUID) -> BugHistory:
        bug = self.get_bug(user, bug_id)
        logs = self.session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == "bug_report", AuditLog.entity_id == str(bug.id))
            .order_by(AuditLog.created_at.asc())
        ).all()
        events: list[HistoryEvent] = []
        for log in logs:
            if not self._history_visible(user, log):
                continue
            events.append(HistoryEvent(action=log.action, at=log.created_at, detail=self._public_detail(user, log.detail or {})))
        return BugHistory(bug_id=bug.id, events=events)

    def add_note(self, user: User, bug_id: uuid.UUID, body: str, visibility: str) -> None:
        if user.role not in _NOTE_ROLES:
            raise ForbiddenError("Only developers, management, or admins can update issues")
        bug = self.get_bug(user, bug_id)
        if not bug.gitlab_issue_iid or not bug.gitlab_project_id:
            raise NotFoundError("GitLab issue is not available for this report")
        prefix = "Internal note" if visibility == "internal" else "Information requested"
        self.reporting.gitlab.add_note(
            project_id=bug.gitlab_project_id,
            issue_iid=bug.gitlab_issue_iid,
            body=f"{prefix}: {body}",
        )
        add_audit(
            self.session,
            action="internal_note" if visibility == "internal" else "info_requested",
            entity_type="bug_report",
            entity_id=str(bug.id),
            actor_user_id=user.id,
            detail={"visibility": visibility, "audience": "developer" if visibility == "internal" else "customer"},
        )
        if visibility == "customer_request":
            self.notifications.enqueue_info_request(bug)
        self.session.commit()

    def record_qa(self, user: User, bug_id: uuid.UUID, result: str, comment: str) -> None:
        if user.role not in _QA_ROLES:
            raise ForbiddenError("Only assigned testers can record QA results")
        bug = self.get_bug(user, bug_id)
        if user.role == Role.TESTER and not linked_to_customer(self.session, user.id, bug.customer_id):
            raise NotFoundError("Bug report not found")
        if not bug.gitlab_issue_iid or not bug.gitlab_project_id:
            raise NotFoundError("GitLab issue is not available for this report")
        self.reporting.gitlab.add_note(
            project_id=bug.gitlab_project_id,
            issue_iid=bug.gitlab_issue_iid,
            body=f"QA result: {result}\n\n{comment}",
        )
        add_audit(
            self.session,
            action="qa_result",
            entity_type="bug_report",
            entity_id=str(bug.id),
            actor_user_id=user.id,
            detail={"result": result, "audience": "customer"},
        )
        self.notifications.enqueue_qa(bug, result, comment)
        self.session.commit()

    def serialize(self, user: User, bug: BugReport) -> BugRead:
        self.session.refresh(bug)
        product = self.session.get(Product, bug.product_id)
        customer = self.session.get(Customer, bug.customer_id)
        attachments = self.session.scalars(select(BugAttachment).where(BugAttachment.bug_report_id == bug.id)).all()
        show_project = user.role in {Role.ADMIN, Role.DEVELOPER, Role.MANAGEMENT}
        return BugRead(
            id=bug.id,
            product_slug=product.slug,
            product_name=product.name,
            customer_code=customer.code,
            severity=bug.severity,
            status=bug.status,
            title=bug.title,
            description=bug.description,
            source=bug.source,
            gitlab_issue_iid=bug.gitlab_issue_iid,
            gitlab_issue_url=bug.gitlab_issue_url,
            gitlab_project_id=bug.gitlab_project_id if show_project else None,
            attachments=[
                AttachmentRead(
                    id=item.id,
                    content_type=item.content_type,
                    size_bytes=item.size_bytes,
                    original_filename=item.original_filename,
                )
                for item in attachments
            ],
            created_at=bug.created_at,
            updated_at=bug.updated_at,
        )

    def product_summaries(self) -> list[ProductSummary]:
        products = self.session.scalars(select(Product).order_by(Product.slug)).all()
        summaries = []
        for product in products:
            counts = self._status_counts(BugReport.product_id == product.id)
            summaries.append(
                ProductSummary(
                    slug=product.slug,
                    name=product.name,
                    opened=counts.get("opened", 0),
                    closed=counts.get("closed", 0),
                    reopened=counts.get("reopened", 0),
                    pending=counts.get("pending_gitlab", 0) + counts.get("failed", 0),
                )
            )
        return summaries

    def team_summaries(self) -> list[TeamSummary]:
        from app.domain.models import DeveloperTeam

        teams = self.session.scalars(select(DeveloperTeam).order_by(DeveloperTeam.slug)).all()
        summaries = []
        for team in teams:
            product_ids = select(Product.id).where(Product.developer_team_id == team.id)
            counts = self._status_counts(BugReport.product_id.in_(product_ids))
            summaries.append(
                TeamSummary(
                    slug=team.slug,
                    name=team.name,
                    opened=counts.get("opened", 0),
                    closed=counts.get("closed", 0),
                    reopened=counts.get("reopened", 0),
                )
            )
        return summaries

    def _status_counts(self, criterion) -> dict[str, int]:
        rows = self.session.execute(
            select(BugReport.status, func.count()).where(criterion).group_by(BugReport.status)
        ).all()
        return {status: int(count) for status, count in rows}

    def _allowed(self, user: User, bug: BugReport) -> bool:
        product = self.session.get(Product, bug.product_id)
        return can_view_bug(
            role=user.role,
            linked_to_customer=linked_to_customer(self.session, user.id, bug.customer_id),
            on_product_team=on_team(self.session, user.id, product.developer_team_id),
        )

    def _history_visible(self, user: User, log: AuditLog) -> bool:
        if user.role not in _CUSTOMER_ROLES:
            return True
        if log.action in {"internal_note", "unauthorized_access", "gitlab_create_failed"}:
            return False
        audience = (log.detail or {}).get("audience")
        return audience not in {"developer", "management"}

    def _public_detail(self, user: User, detail: dict) -> dict:
        if user.role not in _CUSTOMER_ROLES:
            return detail
        hidden = {"internal", "payload", "note", "audience"}
        return {key: value for key, value in detail.items() if key not in hidden}
