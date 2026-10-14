from dataclasses import dataclass, field

from app.domain.enums import ACTIVE_CONVERSATION_STATES, ConversationState, Severity

START_COMMANDS = {"report bug", "report a bug", "bug", "/bug", "start"}
CANCEL_COMMANDS = {"cancel", "stop", "abort"}
YES_COMMANDS = {"yes", "y", "confirm"}
NO_COMMANDS = {"no", "n"}
RETRY_COMMANDS = {"retry"}
MIN_DESCRIPTION_LENGTH = 3


@dataclass(frozen=True)
class ProductOption:
    slug: str
    name: str


@dataclass(frozen=True)
class Reply:
    kind: str
    body: str
    options: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class FlowState:
    state: str
    draft: dict


@dataclass(frozen=True)
class FlowInput:
    text: str | None
    interactive_id: str | None
    has_image: bool
    expired: bool


@dataclass(frozen=True)
class FlowOutput:
    state: str
    draft: dict
    replies: list[Reply]
    action: str


def _norm(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def is_start(text: str | None, interactive_id: str | None = None) -> bool:
    return _norm(interactive_id) in START_COMMANDS or _norm(text) in START_COMMANDS


def _product_reply(products: list[ProductOption]) -> Reply:
    if not products:
        return Reply(kind="text", body="No products are configured. Contact an administrator.")
    return Reply(
        kind="list",
        body="Select the product for this bug report.",
        options=[{"id": product.slug, "title": product.name} for product in products],
    )


def _severity_reply() -> Reply:
    return Reply(
        kind="list",
        body="Select the severity.",
        options=[
            {"id": "sev_low", "title": "Low"},
            {"id": "sev_medium", "title": "Medium"},
            {"id": "sev_high", "title": "High"},
            {"id": "sev_critical", "title": "Critical"},
        ],
    )


def _confirm_reply(draft: dict, products: list[ProductOption]) -> Reply:
    slug = draft.get("product_slug", "")
    name = next((product.name for product in products if product.slug == slug), slug)
    description = draft.get("description", "")
    severity = draft.get("severity", "")
    body = (
        f"Confirm submission?\nProduct: {name}\nSeverity: {severity}\n"
        f"Description: {description}\nReply Yes to create the GitLab issue, or No to revise."
    )
    return Reply(
        kind="buttons",
        body=body,
        options=[{"id": "confirm_yes", "title": "Yes"}, {"id": "confirm_no", "title": "No"}],
    )


def match_product(text: str | None, interactive_id: str | None, products: list[ProductOption]) -> ProductOption | None:
    key = _norm(interactive_id) or _norm(text)
    if not key:
        return None
    for product in products:
        if key == product.slug.lower() or key == product.name.lower():
            return product
    return None


def match_severity(text: str | None, interactive_id: str | None) -> str | None:
    raw = _norm(interactive_id) or _norm(text)
    raw = raw.removeprefix("sev_")
    if raw in {item.value for item in Severity}:
        return raw
    return None


def _idle_output(incoming: FlowInput, products: list[ProductOption], draft: dict | None = None) -> FlowOutput:
    if is_start(incoming.text, incoming.interactive_id):
        return FlowOutput(
            state=ConversationState.AWAITING_PRODUCT,
            draft={},
            replies=[_product_reply(products)],
            action="none",
        )
    return FlowOutput(
        state=ConversationState.IDLE,
        draft=draft or {},
        replies=[Reply(kind="text", body='Reply "Report Bug" to start a bug report.')],
        action="none",
    )


def step(current: FlowState, incoming: FlowInput, products: list[ProductOption]) -> FlowOutput:
    text = _norm(incoming.text)
    interactive = _norm(incoming.interactive_id)
    command = interactive or text
    draft = dict(current.draft)
    state = current.state

    if incoming.expired and state in ACTIVE_CONVERSATION_STATES:
        if is_start(incoming.text, incoming.interactive_id):
            return FlowOutput(
                state=ConversationState.AWAITING_PRODUCT,
                draft={},
                replies=[Reply(kind="text", body="The previous report expired."), _product_reply(products)],
                action="none",
            )
        return FlowOutput(
            state=ConversationState.FAILED,
            draft=draft,
            replies=[Reply(kind="text", body='This report expired. Reply "Report Bug" to start again.')],
            action="none",
        )

    if state in ACTIVE_CONVERSATION_STATES and command in CANCEL_COMMANDS:
        return FlowOutput(
            state=ConversationState.IDLE,
            draft={},
            replies=[Reply(kind="text", body="Report cancelled. No GitLab issue was created.")],
            action="cancel",
        )

    if state in {ConversationState.IDLE, ConversationState.COMPLETED, ConversationState.FAILED}:
        if state == ConversationState.FAILED and command in RETRY_COMMANDS and _draft_ready(draft):
            return FlowOutput(state=ConversationState.SUBMITTING, draft=draft, replies=[], action="submit")
        if command in CANCEL_COMMANDS:
            return FlowOutput(
                state=state if state != ConversationState.IDLE else ConversationState.IDLE,
                draft=draft if state != ConversationState.IDLE else {},
                replies=[Reply(kind="text", body="There is no active report.")],
                action="none",
            )
        return _idle_output(incoming, products, draft if state == ConversationState.FAILED else {})

    if state == ConversationState.SUBMITTING:
        return FlowOutput(
            state=state,
            draft=draft,
            replies=[Reply(kind="text", body="Your report is being submitted.")],
            action="none",
        )

    if state == ConversationState.AWAITING_PRODUCT:
        product = match_product(incoming.text, incoming.interactive_id, products)
        if product is None:
            return FlowOutput(
                state=state,
                draft=draft,
                replies=[
                    Reply(kind="text", body="That product is not available."),
                    _product_reply(products),
                ],
                action="none",
            )
        draft["product_slug"] = product.slug
        return FlowOutput(
            state=ConversationState.AWAITING_IMAGE,
            draft=draft,
            replies=[Reply(kind="text", body=f"Send a screenshot for {product.name}. JPEG, PNG, or WebP.")],
            action="none",
        )

    if state == ConversationState.AWAITING_IMAGE:
        if not incoming.has_image:
            return FlowOutput(
                state=state,
                draft=draft,
                replies=[Reply(kind="text", body="Please send a screenshot (JPEG, PNG, or WebP).")],
                action="none",
            )
        return FlowOutput(
            state=ConversationState.AWAITING_DESCRIPTION,
            draft=draft,
            replies=[Reply(kind="text", body="Describe the issue.")],
            action="none",
        )

    if state == ConversationState.AWAITING_DESCRIPTION:
        description = " ".join((incoming.text or "").split())
        if len(description) < MIN_DESCRIPTION_LENGTH:
            return FlowOutput(
                state=state,
                draft=draft,
                replies=[Reply(kind="text", body="Please describe the issue in a few words.")],
                action="none",
            )
        draft["description"] = description
        return FlowOutput(
            state=ConversationState.AWAITING_SEVERITY,
            draft=draft,
            replies=[_severity_reply()],
            action="none",
        )

    if state == ConversationState.AWAITING_SEVERITY:
        severity = match_severity(incoming.text, incoming.interactive_id)
        if severity is None:
            return FlowOutput(
                state=state,
                draft=draft,
                replies=[Reply(kind="text", body="Choose low, medium, high, or critical."), _severity_reply()],
                action="none",
            )
        draft["severity"] = severity
        return FlowOutput(
            state=ConversationState.AWAITING_CONFIRMATION,
            draft=draft,
            replies=[_confirm_reply(draft, products)],
            action="none",
        )

    if state == ConversationState.AWAITING_CONFIRMATION:
        if interactive in {"confirm_yes", "yes"} or text in YES_COMMANDS:
            return FlowOutput(
                state=ConversationState.SUBMITTING,
                draft=draft,
                replies=[Reply(kind="text", body="Submitting your report.")],
                action="submit",
            )
        if interactive in {"confirm_no", "no"} or text in NO_COMMANDS:
            draft.pop("description", None)
            return FlowOutput(
                state=ConversationState.AWAITING_DESCRIPTION,
                draft=draft,
                replies=[Reply(kind="text", body="Send a revised description, or reply cancel.")],
                action="none",
            )
        return FlowOutput(
            state=state,
            draft=draft,
            replies=[Reply(kind="text", body="Reply Yes to submit or No to revise.")],
            action="none",
        )

    return FlowOutput(
        state=ConversationState.IDLE,
        draft={},
        replies=[Reply(kind="text", body='Reply "Report Bug" to start a bug report.')],
        action="none",
    )


def _draft_ready(draft: dict) -> bool:
    return bool(draft.get("product_slug") and draft.get("description") and draft.get("severity") and draft.get("attachment_id"))
