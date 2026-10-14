import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import correlation_id_var
from app.core.security import phone_digits
from app.domain.enums import Role
from app.domain.models import (
    BugReport,
    Customer,
    CustomerUser,
    DeveloperTeam,
    NotificationOutbox,
    NotificationRule,
    Product,
    TeamMember,
    User,
)
from app.services.messages import customer_message, developer_message, management_message


class NotificationService:
    def __init__(self, session: Session):
        self.session = session

    def enqueue_submission(self, bug: BugReport) -> None:
        context = self._context(bug)
        reference = bug.gitlab_issue_url or bug.gitlab_issue_iid or str(bug.id)
        for user in self._customer_recipients(bug):
            body = customer_message(
                event_type="bug_submitted",
                report_id=str(bug.id),
                product_name=context["product"].name,
                reference=reference,
                status=bug.status,
                qa_result=None,
            )
            self.enqueue(
                bug=bug,
                user=user,
                audience="customer",
                event_type="bug_submitted",
                body=body,
                dedupe_key=f"{bug.id}:bug_submitted:customer:{user.id}",
            )
        developer_body = developer_message(
            event_type="issue_assigned",
            product_name=context["product"].name,
            severity=bug.severity,
            customer_name=context["customer"].name,
            reference=reference,
            title=bug.title,
            description=bug.description,
        )
        for user in self._developer_recipients(context["product"]):
            self.enqueue(
                bug=bug,
                user=user,
                audience="developer",
                event_type="issue_assigned",
                body=developer_body,
                dedupe_key=f"{bug.id}:issue_assigned:developer:{user.id}",
            )
        management_body = management_message(
            event_type="bug_submitted",
            report_id=str(bug.id),
            product_name=context["product"].name,
            team_name=context["team"].name,
            severity=bug.severity,
            product_open=context["product_open"],
            team_open=context["team_open"],
        )
        for user in self._management_recipients():
            self.enqueue(
                bug=bug,
                user=user,
                audience="management",
                event_type="bug_submitted",
                body=management_body,
                dedupe_key=f"{bug.id}:bug_submitted:management:{user.id}",
            )

    def enqueue_gitlab_events(self, bug: BugReport, events: list[tuple[str, str]], source_id: str) -> None:
        context = self._context(bug)
        reference = bug.gitlab_issue_url or bug.gitlab_issue_iid or str(bug.id)
        for event_type, audience in events:
            if audience == "customer":
                body = customer_message(
                    event_type=event_type,
                    report_id=str(bug.id),
                    product_name=context["product"].name,
                    reference=reference,
                    status=bug.status,
                    qa_result=None,
                )
                recipients = self._customer_recipients(bug)
            elif audience == "developer":
                body = developer_message(
                    event_type=event_type,
                    product_name=context["product"].name,
                    severity=bug.severity,
                    customer_name=context["customer"].name,
                    reference=reference,
                    title=bug.title,
                    description=bug.description,
                )
                recipients = self._developer_recipients(context["product"])
            else:
                body = management_message(
                    event_type=event_type,
                    report_id=str(bug.id),
                    product_name=context["product"].name,
                    team_name=context["team"].name,
                    severity=bug.severity,
                    product_open=context["product_open"],
                    team_open=context["team_open"],
                )
                recipients = self._management_recipients()
            suffix = "assigned" if event_type == "issue_assigned" else source_id
            for user in recipients:
                self.enqueue(
                    bug=bug,
                    user=user,
                    audience=audience,
                    event_type=event_type,
                    body=body,
                    dedupe_key=f"{bug.id}:{event_type}:{audience}:{user.id}:{suffix}",
                )

    def enqueue_qa(self, bug: BugReport, result: str, comment: str) -> None:
        context = self._context(bug)
        reference = bug.gitlab_issue_url or bug.gitlab_issue_iid or str(bug.id)
        customer_body = customer_message(
            event_type="qa_result",
            report_id=str(bug.id),
            product_name=context["product"].name,
            reference=reference,
            status=bug.status,
            qa_result=result,
        )
        for user in self._customer_recipients(bug):
            self.enqueue(
                bug=bug,
                user=user,
                audience="customer",
                event_type="qa_result",
                body=customer_body,
                dedupe_key=f"{bug.id}:qa_result:customer:{user.id}:{result}:{comment[:40]}",
            )
        developer_body = developer_message(
            event_type="qa_feedback",
            product_name=context["product"].name,
            severity=bug.severity,
            customer_name=context["customer"].name,
            reference=reference,
            title=bug.title,
            description=comment,
        )
        for user in self._developer_recipients(context["product"]):
            self.enqueue(
                bug=bug,
                user=user,
                audience="developer",
                event_type="qa_feedback",
                body=developer_body,
                dedupe_key=f"{bug.id}:qa_feedback:developer:{user.id}:{result}:{comment[:40]}",
            )

    def enqueue_info_request(self, bug: BugReport) -> None:
        context = self._context(bug)
        reference = bug.gitlab_issue_url or bug.gitlab_issue_iid or str(bug.id)
        body = customer_message(
            event_type="info_requested",
            report_id=str(bug.id),
            product_name=context["product"].name,
            reference=reference,
            status=bug.status,
            qa_result=None,
        )
        stamp = datetime.now(timezone.utc).isoformat()
        for user in self._customer_recipients(bug):
            self.enqueue(
                bug=bug,
                user=user,
                audience="customer",
                event_type="info_requested",
                body=body,
                dedupe_key=f"{bug.id}:info_requested:customer:{user.id}:{stamp}",
            )

    def enqueue_reply(self, *, conversation, user: User | None, kind: str, body: str, options: list[dict], dedupe_key: str) -> None:
        self._insert(
            bug=None,
            user=user,
            audience="conversation",
            event_type="conversation_reply",
            body=body,
            kind=kind,
            options=options,
            dedupe_key=dedupe_key,
            destination=phone_digits(conversation.wa_id),
            conversation_id=conversation.id,
            product_id=None,
        )

    def enqueue(
        self,
        *,
        bug: BugReport,
        user: User,
        audience: str,
        event_type: str,
        body: str,
        dedupe_key: str,
    ) -> None:
        product_id = bug.product_id
        if not self.rule_enabled(event_type, audience, product_id):
            return
        self._insert(
            bug=bug,
            user=user,
            audience=audience,
            event_type=event_type,
            body=body,
            kind="text",
            options=[],
            dedupe_key=dedupe_key,
            destination=phone_digits(user.phone_e164),
            conversation_id=bug.conversation_id,
            product_id=product_id,
        )

    def rule_enabled(self, event_type: str, audience: str, product_id: uuid.UUID | None) -> bool:
        if product_id is not None:
            specific = self.session.scalar(
                select(NotificationRule).where(
                    NotificationRule.event_type == event_type,
                    NotificationRule.audience == audience,
                    NotificationRule.product_id == product_id,
                )
            )
            if specific is not None:
                return specific.enabled
        generic = self.session.scalar(
            select(NotificationRule).where(
                NotificationRule.event_type == event_type,
                NotificationRule.audience == audience,
                NotificationRule.product_id.is_(None),
            )
        )
        if generic is not None:
            return generic.enabled
        return True

    def _insert(
        self,
        *,
        bug: BugReport | None,
        user: User | None,
        audience: str,
        event_type: str,
        body: str,
        kind: str,
        options: list[dict],
        dedupe_key: str,
        destination: str,
        conversation_id,
        product_id,
    ) -> None:
        del product_id
        row = NotificationOutbox(
            bug_report_id=bug.id if bug else None,
            conversation_id=conversation_id,
            recipient_user_id=user.id if user else None,
            audience=audience,
            channel="whatsapp",
            event_type=event_type,
            payload={"to": destination, "kind": kind, "body": body, "options": options},
            status="pending",
            attempts=0,
            next_attempt_at=datetime.now(timezone.utc),
            dedupe_key=dedupe_key,
            correlation_id=correlation_id_var.get(),
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            return

    def _context(self, bug: BugReport) -> dict:
        product = self.session.get(Product, bug.product_id)
        customer = self.session.get(Customer, bug.customer_id)
        team = self.session.get(DeveloperTeam, product.developer_team_id)
        return {
            "product": product,
            "customer": customer,
            "team": team,
            "product_open": self._open_count([product.id]),
            "team_open": self._open_count(self._team_product_ids(team.id)),
        }

    def _open_count(self, product_ids: list[uuid.UUID]) -> int:
        if not product_ids:
            return 0
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(BugReport)
                .where(BugReport.product_id.in_(product_ids), BugReport.status.in_(("opened", "reopened")))
            )
            or 0
        )

    def _team_product_ids(self, team_id: uuid.UUID) -> list[uuid.UUID]:
        return list(self.session.scalars(select(Product.id).where(Product.developer_team_id == team_id)).all())

    def _customer_recipients(self, bug: BugReport) -> list[User]:
        found: dict[uuid.UUID, User] = {}
        reporter = self.session.get(User, bug.reporter_user_id)
        if reporter is not None:
            found[reporter.id] = reporter
        linked = self.session.scalars(
            select(User)
            .join(CustomerUser, CustomerUser.user_id == User.id)
            .where(
                CustomerUser.customer_id == bug.customer_id,
                User.role == Role.CUSTOMER,
                User.is_active.is_(True),
            )
        ).all()
        for user in linked:
            found[user.id] = user
        return list(found.values())

    def _developer_recipients(self, product: Product) -> list[User]:
        return list(
            self.session.scalars(
                select(User)
                .join(TeamMember, TeamMember.user_id == User.id)
                .where(
                    TeamMember.team_id == product.developer_team_id,
                    User.role.in_((Role.DEVELOPER, Role.ADMIN)),
                    User.is_active.is_(True),
                )
            ).all()
        )

    def _management_recipients(self) -> list[User]:
        return list(
            self.session.scalars(
                select(User).where(User.role == Role.MANAGEMENT, User.is_active.is_(True))
            ).all()
        )
