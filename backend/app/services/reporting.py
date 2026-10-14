import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import IntegrationError, ValidationAppError
from app.domain.enums import BugStatus
from app.domain.models import BugAttachment, BugReport, Customer, Product, User
from app.integrations.gitlab.mock import MockGitLabProvider
from app.services.attachments import AttachmentService
from app.services.audit import add_audit
from app.services.notifications import NotificationService

logger = logging.getLogger(__name__)


def build_title(product_name: str, severity: str, description: str) -> str:
    summary = " ".join(description.split())
    if len(summary) > 80:
        summary = summary[:77] + "..."
    return f"[{severity}] {product_name}: {summary}"[:255]


def build_issue_description(
    *,
    bug_id: uuid.UUID,
    product_name: str,
    severity: str,
    customer_name: str,
    customer_code: str,
    reporter_name: str,
    source: str,
    description: str,
    screenshot_markdown: str | None,
) -> str:
    screenshot = f"\n\n## Screenshot\n\n{screenshot_markdown}" if screenshot_markdown else ""
    return (
        "## Bug report\n\n"
        f"- Product: {product_name}\n"
        f"- Severity: {severity}\n"
        f"- Customer: {customer_name} ({customer_code})\n"
        f"- Reporter: {reporter_name}\n"
        f"- Source: {source}\n"
        f"- Report ID: {bug_id}\n\n"
        f"## Description\n\n{description}"
        f"{screenshot}\n\n"
        f"<!-- bug-report-id:{bug_id} -->"
    )


def labels_for(product: Product, default_labels: list[str]) -> list[str]:
    configured = [str(label).strip() for label in (product.default_labels or []) if str(label).strip()]
    if not configured:
        configured = list(default_labels)
        if product.slug not in configured:
            configured.append(product.slug)
    unique: list[str] = []
    for label in configured:
        if label not in unique:
            unique.append(label)
    return unique


class ReportingService:
    def __init__(self, session: Session, gitlab, settings: Settings, attachments: AttachmentService):
        self.session = session
        self.gitlab = gitlab
        self.settings = settings
        self.attachments = attachments
        self.notifications = NotificationService(session)

    def submit(
        self,
        *,
        reporter: User,
        customer: Customer,
        product: Product,
        description: str,
        severity: str,
        source: str,
        idempotency_key: str | None,
        attachment: BugAttachment | None,
        correlation_id: str | None,
        conversation_id: uuid.UUID | None,
    ) -> BugReport:
        bug = self._find_existing(idempotency_key) if idempotency_key else None
        if bug and bug.customer_id != customer.id:
            raise ValidationAppError("Idempotency key is already used by another customer")
        if bug is None:
            bug = self._insert(
                reporter=reporter,
                customer=customer,
                product=product,
                description=description,
                severity=severity,
                source=source,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                conversation_id=conversation_id,
            )
        if attachment is not None:
            self.attachments.bind_to_bug(attachment, bug.id)
            self.session.commit()
        if not bug.gitlab_issue_iid:
            self._align_mock_sequence()
            self._create_gitlab_issue(bug, product, customer, reporter, attachment)
        try:
            self.notifications.enqueue_submission(bug)
            self.session.commit()
        except Exception:
            self.session.rollback()
            logger.exception("notification enqueue failed", extra={"bug_id": str(bug.id)})
            bug = self.session.get(BugReport, bug.id)
        return bug

    def _find_existing(self, idempotency_key: str) -> BugReport | None:
        return self.session.scalar(select(BugReport).where(BugReport.idempotency_key == idempotency_key))

    def _insert(self, **kwargs) -> BugReport:
        product: Product = kwargs["product"]
        description = kwargs["description"]
        severity = kwargs["severity"]
        bug = BugReport(
            customer_id=kwargs["customer"].id,
            reporter_user_id=kwargs["reporter"].id,
            product_id=product.id,
            conversation_id=kwargs["conversation_id"],
            title=build_title(product.name, severity, description),
            description=description,
            severity=severity,
            status=BugStatus.PENDING_GITLAB,
            source=kwargs["source"],
            idempotency_key=kwargs["idempotency_key"],
            correlation_id=kwargs["correlation_id"],
        )
        try:
            with self.session.begin_nested():
                self.session.add(bug)
                self.session.flush()
        except IntegrityError:
            existing = self._find_existing(kwargs["idempotency_key"]) if kwargs["idempotency_key"] else None
            if existing is None:
                raise
            return existing
        add_audit(
            self.session,
            action="bug_submitted",
            entity_type="bug_report",
            entity_id=str(bug.id),
            actor_user_id=kwargs["reporter"].id,
            correlation_id=kwargs["correlation_id"],
            detail={"source": kwargs["source"], "product_slug": product.slug, "severity": severity},
        )
        self.session.commit()
        return bug

    def _create_gitlab_issue(
        self,
        bug: BugReport,
        product: Product,
        customer: Customer,
        reporter: User,
        attachment: BugAttachment | None,
    ) -> None:
        marker = f"bug-report-id:{bug.id}"
        screenshot = None
        try:
            if attachment is not None and not attachment.gitlab_markdown:
                content = self.attachments.store.read(attachment.storage_key)
                screenshot = self.gitlab.upload_file(
                    project_id=product.gitlab_project_id,
                    filename=attachment.original_filename,
                    content=content,
                    content_type=attachment.content_type,
                )
                attachment.gitlab_markdown = screenshot
                self.session.commit()
            elif attachment is not None:
                screenshot = attachment.gitlab_markdown
            description = build_issue_description(
                bug_id=bug.id,
                product_name=product.name,
                severity=bug.severity,
                customer_name=customer.name,
                customer_code=customer.code,
                reporter_name=reporter.display_name,
                source=bug.source,
                description=bug.description,
                screenshot_markdown=screenshot,
            )
            labels = labels_for(product, self.settings.label_defaults)
            issue = self.gitlab.create_issue(
                project_id=product.gitlab_project_id,
                title=bug.title,
                description=description,
                labels=labels,
                assignee_id=product.default_assignee_gitlab_id,
                marker=marker,
            )
            if self._issue_owned_by_other(issue.project_id or product.gitlab_project_id, issue.iid, bug.id):
                if not isinstance(self.gitlab, MockGitLabProvider):
                    raise IntegrationError(
                        "GitLab issue identifier is already mapped to another report",
                        retryable=True,
                        status_code=409,
                    )
                self._align_mock_sequence()
                issue = self.gitlab.reissue(
                    project_id=product.gitlab_project_id,
                    title=bug.title,
                    description=description,
                    labels=labels,
                    assignee_id=product.default_assignee_gitlab_id,
                    marker=marker,
                )
                if self._issue_owned_by_other(issue.project_id or product.gitlab_project_id, issue.iid, bug.id):
                    raise IntegrationError(
                        "GitLab issue identifier is already mapped to another report",
                        retryable=True,
                        status_code=409,
                    )
        except IntegrationError:
            bug.status = BugStatus.FAILED
            add_audit(
                self.session,
                action="gitlab_create_failed",
                entity_type="bug_report",
                entity_id=str(bug.id),
                actor_user_id=reporter.id,
                detail={"product_slug": product.slug},
            )
            self.session.commit()
            raise
        bug.gitlab_issue_id = issue.id
        bug.gitlab_issue_iid = issue.iid
        bug.gitlab_project_id = issue.project_id or product.gitlab_project_id
        bug.gitlab_issue_url = issue.web_url
        bug.status = BugStatus.CLOSED if issue.state == "closed" else BugStatus.OPENED
        add_audit(
            self.session,
            action="gitlab_issue_created",
            entity_type="bug_report",
            entity_id=str(bug.id),
            actor_user_id=reporter.id,
            detail={"gitlab_issue_iid": issue.iid, "gitlab_project_id": bug.gitlab_project_id},
        )
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            if "uq_bug_reports_gitlab_issue" not in str(exc):
                raise
            raise IntegrationError(
                "GitLab issue identifier is already mapped to another report",
                retryable=True,
                status_code=409,
            ) from exc

    def _align_mock_sequence(self) -> None:
        if not isinstance(self.gitlab, MockGitLabProvider):
            return
        highest = 0
        iids = self.session.scalars(select(BugReport.gitlab_issue_iid).where(BugReport.gitlab_issue_iid.is_not(None))).all()
        for iid in iids:
            if str(iid).isdigit():
                highest = max(highest, int(iid))
        self.gitlab.ensure_sequence_at_least(highest)

    def _issue_owned_by_other(self, project_id: str, iid: str, bug_id: uuid.UUID) -> bool:
        owner = self.session.scalar(
            select(BugReport).where(
                BugReport.gitlab_project_id == str(project_id),
                BugReport.gitlab_issue_iid == str(iid),
                BugReport.id != bug_id,
            )
        )
        return owner is not None
