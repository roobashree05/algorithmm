from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import IntegrationError, ValidationAppError
from app.core.security import normalize_phone, phone_digits
from app.domain.conversation_flow import RETRY_COMMANDS, FlowInput, FlowState, ProductOption, Reply, step
from app.domain.enums import ACTIVE_CONVERSATION_STATES, ConversationState, Role
from app.domain.models import BugAttachment, Conversation, Customer, InboundMessage, Product, User
from app.domain.whatsapp_events import ParsedInbound
from app.repositories.access import customers_for_user, user_by_phone
from app.services.attachments import AttachmentService
from app.services.audit import add_audit
from app.services.notifications import NotificationService
from app.services.reporting import ReportingService


class ConversationService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        whatsapp,
        reporting: ReportingService,
        attachments: AttachmentService,
    ):
        self.session = session
        self.settings = settings
        self.whatsapp = whatsapp
        self.reporting = reporting
        self.attachments = attachments
        self.notifications = NotificationService(session)

    def handle_inbound(self, message: ParsedInbound, body_hash: str) -> str:
        if self.session.scalar(select(InboundMessage.id).where(InboundMessage.provider_message_id == message.message_id)):
            return "duplicate"
        try:
            phone = normalize_phone(message.wa_id)
        except ValueError:
            self._reply_unknown(message, body_hash, "This phone number cannot be used to report bugs.")
            return "rejected"
        user = user_by_phone(self.session, phone)
        if user is None:
            self._reply_unknown(message, body_hash, "Your phone number is not authorized to report bugs.")
            return "unauthorized"
        if user.role not in {Role.CUSTOMER, Role.TESTER}:
            self._reply_to_user(user, message, body_hash, [Reply(kind="text", body="This WhatsApp number is not allowed to submit bug reports.")])
            return "forbidden"
        links = customers_for_user(self.session, user.id)
        if len(links) != 1:
            self._reply_to_user(
                user,
                message,
                body_hash,
                [Reply(kind="text", body="This number must be mapped to exactly one customer account before it can report bugs.")],
            )
            return "forbidden"
        customer = links[0]
        products = list(self.session.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.name)).all())
        options = [ProductOption(slug=product.slug, name=product.name) for product in products]
        latest = self.session.scalar(
            select(Conversation).where(Conversation.wa_id == phone_digits(message.wa_id)).order_by(Conversation.created_at.desc())
        )
        now = datetime.now(timezone.utc)
        command = " ".join(((message.interactive_id or message.text or "").strip().lower()).split())
        active = latest if latest and latest.state in ACTIVE_CONVERSATION_STATES else None
        retrying = bool(latest and latest.state == ConversationState.FAILED and command in RETRY_COMMANDS)
        base = active or (latest if retrying else None)
        expired = bool(base and base.expires_at and base.expires_at < now and base.state in ACTIVE_CONVERSATION_STATES)

        attachment = None
        if (
            message.image_media_id
            and base
            and base.state == ConversationState.AWAITING_IMAGE
            and not expired
        ):
            try:
                content, downloaded_type = self.whatsapp.download_media(message.image_media_id)
                attachment = self.attachments.store_temp(
                    content,
                    message.image_mime or downloaded_type,
                    "screenshot",
                )
            except (ValidationAppError, IntegrationError):
                self._save(
                    base=base,
                    user=user,
                    customer=customer,
                    message=message,
                    body_hash=body_hash,
                    state=base.state,
                    draft=dict(base.draft or {}),
                    replies=[Reply(kind="text", body="That image could not be accepted. Send a JPEG, PNG, or WebP screenshot under the size limit.")],
                    now=now,
                )
                return "invalid_image"

        if base is not None:
            draft = dict(base.draft or {})
            state = base.state
        elif latest and latest.state == ConversationState.FAILED:
            draft = dict(latest.draft or {})
            state = latest.state
        elif latest and latest.state in {ConversationState.COMPLETED, ConversationState.IDLE}:
            draft = {}
            state = latest.state
        else:
            draft = {}
            state = ConversationState.IDLE
        if attachment is not None:
            draft["attachment_id"] = str(attachment.id)
        output = step(
            FlowState(state=state, draft=draft),
            FlowInput(
                text=message.text,
                interactive_id=message.interactive_id,
                has_image=attachment is not None,
                expired=expired,
            ),
            options,
        )
        if base is None and output.state == state and output.action == "none" and latest and latest.state == output.state:
            target_base = None
        else:
            target_base = base
        if output.state in ACTIVE_CONVERSATION_STATES or output.action == "submit" or target_base is not None:
            conversation = target_base
        else:
            conversation = None
        saved = self._save(
            base=conversation,
            user=user,
            customer=customer,
            message=message,
            body_hash=body_hash,
            state=output.state if output.action != "submit" else ConversationState.SUBMITTING,
            draft=output.draft,
            replies=output.replies,
            now=now,
        )
        if attachment is not None:
            attachment.conversation_id = saved.id
            self.session.commit()
        if output.action == "cancel":
            add_audit(
                self.session,
                action="conversation_cancelled",
                entity_type="conversation",
                entity_id=str(saved.id),
                actor_user_id=user.id,
                detail={},
            )
            self.session.commit()
        if output.action == "submit":
            self._submit(saved, user, customer)
        return "ok"

    def _submit(self, conversation: Conversation, user: User, customer: Customer) -> None:
        draft = dict(conversation.draft or {})
        product = self.session.scalar(
            select(Product).where(Product.slug == draft.get("product_slug"), Product.is_active.is_(True))
        )
        attachment = None
        if draft.get("attachment_id"):
            attachment = self.session.get(BugAttachment, UUID(str(draft["attachment_id"])))
        if product is None or attachment is None or not draft.get("description") or not draft.get("severity"):
            self._fail(conversation, user, "The report is incomplete. Reply \"Report Bug\" to start again.")
            return
        try:
            bug = self.reporting.submit(
                reporter=user,
                customer=customer,
                product=product,
                description=draft["description"],
                severity=draft["severity"],
                source="whatsapp",
                idempotency_key=f"conversation:{conversation.id}",
                attachment=attachment,
                correlation_id=conversation.correlation_id,
                conversation_id=conversation.id,
            )
        except IntegrationError:
            conversation.state = ConversationState.FAILED
            conversation.draft = draft
            self.notifications.enqueue_reply(
                conversation=conversation,
                user=user,
                kind="text",
                body="GitLab could not create the issue. Reply retry to try again.",
                options=[],
                dedupe_key=f"{conversation.id}:gitlab-failed",
            )
            self.session.commit()
            return
        draft["bug_report_id"] = str(bug.id)
        conversation.state = ConversationState.COMPLETED
        conversation.draft = draft
        reference = bug.gitlab_issue_url or bug.gitlab_issue_iid or str(bug.id)
        self.notifications.enqueue_reply(
            conversation=conversation,
            user=user,
            kind="text",
            body=f"Bug report submitted. Reference: {reference}.",
            options=[],
            dedupe_key=f"{conversation.id}:submitted:{bug.id}",
        )
        self.session.commit()

    def _fail(self, conversation: Conversation, user: User, text: str) -> None:
        conversation.state = ConversationState.FAILED
        self.notifications.enqueue_reply(
            conversation=conversation,
            user=user,
            kind="text",
            body=text,
            options=[],
            dedupe_key=f"{conversation.id}:failed:{text[:40]}",
        )
        self.session.commit()

    def _save(
        self,
        *,
        base: Conversation | None,
        user: User,
        customer: Customer,
        message: ParsedInbound,
        body_hash: str,
        state: str,
        draft: dict,
        replies: list[Reply],
        now: datetime,
    ) -> Conversation:
        if base is None:
            conversation = Conversation(
                wa_id=phone_digits(message.wa_id),
                user_id=user.id,
                customer_id=customer.id,
                state=state,
                draft=dict(draft),
                correlation_id=message.message_id[:100],
            )
            self.session.add(conversation)
        else:
            conversation = base
            conversation.state = state
            conversation.draft = dict(draft)
            conversation.user_id = user.id
            conversation.customer_id = customer.id
        if state in ACTIVE_CONVERSATION_STATES or state == ConversationState.SUBMITTING:
            conversation.expires_at = now + timedelta(minutes=self.settings.conversation_ttl_minutes)
        self.session.add(
            InboundMessage(
                provider_message_id=message.message_id,
                conversation_id=None,
                wa_id=phone_digits(message.wa_id),
                payload_sha256=body_hash,
            )
        )
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise
        inbound = self.session.scalar(select(InboundMessage).where(InboundMessage.provider_message_id == message.message_id))
        if inbound is not None:
            inbound.conversation_id = conversation.id
        for index, reply in enumerate(replies):
            self.notifications.enqueue_reply(
                conversation=conversation,
                user=user,
                kind=reply.kind,
                body=reply.body,
                options=reply.options,
                dedupe_key=f"{conversation.id}:{message.message_id}:{index}",
            )
        self.session.commit()
        return conversation

    def _reply_unknown(self, message: ParsedInbound, body_hash: str, text: str) -> None:
        conversation = Conversation(
            wa_id=phone_digits(message.wa_id) or message.wa_id[-20:],
            state=ConversationState.FAILED,
            draft={},
            correlation_id=message.message_id[:100],
        )
        self.session.add(conversation)
        self.session.flush()
        self.session.add(
            InboundMessage(
                provider_message_id=message.message_id,
                conversation_id=conversation.id,
                wa_id=conversation.wa_id,
                payload_sha256=body_hash,
            )
        )
        self.notifications.enqueue_reply(
            conversation=conversation,
            user=None,
            kind="text",
            body=text,
            options=[],
            dedupe_key=f"unknown:{message.message_id}",
        )
        add_audit(
            self.session,
            action="unauthorized_whatsapp",
            entity_type="conversation",
            entity_id=str(conversation.id),
            detail={"wa_id_suffix": conversation.wa_id[-4:]},
        )
        self.session.commit()

    def _reply_to_user(self, user: User, message: ParsedInbound, body_hash: str, replies: list[Reply]) -> None:
        now = datetime.now(timezone.utc)
        links = customers_for_user(self.session, user.id)
        customer = links[0] if links else None
        if customer is None:
            self._reply_unknown(message, body_hash, replies[0].body if replies else "Not authorized")
            return
        self._save(
            base=None,
            user=user,
            customer=customer,
            message=message,
            body_hash=body_hash,
            state=ConversationState.IDLE,
            draft={},
            replies=replies,
            now=now,
        )
