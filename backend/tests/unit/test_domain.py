import hashlib
import hmac

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.redaction import redact, redact_text
from app.core.security import safe_compare, verify_meta_signature
from app.domain.conversation_flow import FlowInput, FlowState, ProductOption, step
from app.domain.enums import ConversationState
from app.domain.gitlab_events import map_gitlab_status, notification_events_for_gitlab, parse_gitlab_issue_event
from app.domain.permissions import can_view_bug, delivery_allowed
from app.domain.whatsapp_events import parse_whatsapp_payload
from app.integrations.gitlab.mock import MockGitLabProvider
from app.integrations.gitlab.rest import GitLabRestProvider
from app.integrations.whatsapp.capabilities import group_messaging_available
from app.integrations.whatsapp.cloud_api import CloudWhatsAppProvider
from app.services.attachments import AttachmentStore, detect_image_type
from app.services.backoff import exponential_backoff_seconds
from app.services.messages import customer_message, developer_message
from tests.helpers import PNG


PRODUCTS = [ProductOption("try-on", "Try-On"), ProductOption("inventory", "Inventory")]


def test_configuration_requires_real_provider_credentials():
    with pytest.raises(ValidationError):
        Settings(whatsapp_provider="cloud_api")
    with pytest.raises(ValidationError):
        Settings(gitlab_provider="rest")


def test_mock_configuration_allows_empty_credentials():
    settings = Settings(whatsapp_provider="mock", gitlab_provider="mock", whatsapp_access_token="")
    assert settings.whatsapp_provider == "mock"
    assert settings.gitlab_provider == "mock"


def test_redaction_hides_tokens():
    cleaned = redact({"authorization": "Bearer secret-token", "nested": {"access_token": "abc"}, "ok": "visible"})
    assert cleaned["authorization"] == "[REDACTED]"
    assert cleaned["nested"]["access_token"] == "[REDACTED]"
    assert cleaned["ok"] == "visible"
    assert "secret-token" not in redact_text("Authorization: Bearer secret-token")


def test_backoff_is_exponential_and_capped():
    assert exponential_backoff_seconds(1, 2, 30) == 2
    assert exponential_backoff_seconds(2, 2, 30) == 4
    assert exponential_backoff_seconds(3, 2, 30) == 8
    assert exponential_backoff_seconds(10, 2, 30) == 30


def test_signature_comparison_rejects_bad_and_unequal_lengths():
    body = b'{"ok":true}'
    secret = "test-whatsapp-secret"
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_meta_signature(secret, body, good) is True
    assert verify_meta_signature(secret, body, "sha256=deadbeef") is False
    assert verify_meta_signature("", body, good) is False
    assert safe_compare("abc", "abcd") is False


def test_permissions_isolate_customers_and_teams():
    assert can_view_bug(role="CUSTOMER", linked_to_customer=True, on_product_team=False) is True
    assert can_view_bug(role="CUSTOMER", linked_to_customer=False, on_product_team=False) is False
    assert can_view_bug(role="TESTER", linked_to_customer=False, on_product_team=True) is False
    assert can_view_bug(role="DEVELOPER", linked_to_customer=False, on_product_team=True) is True
    assert can_view_bug(role="DEVELOPER", linked_to_customer=True, on_product_team=False) is False
    assert can_view_bug(role="MANAGEMENT", linked_to_customer=False, on_product_team=False) is True
    assert delivery_allowed(
        audience="management",
        recipient_role="CUSTOMER",
        recipient_active=True,
        is_reporter=False,
        linked_to_customer=True,
        on_team=False,
    ) is False
    assert delivery_allowed(
        audience="customer",
        recipient_role="CUSTOMER",
        recipient_active=True,
        is_reporter=False,
        linked_to_customer=True,
        on_team=False,
    ) is True


def test_customer_messages_do_not_include_internal_description():
    text = customer_message(
        event_type="bug_submitted",
        report_id="12345678-aaaa",
        product_name="Try-On",
        reference="https://gitlab.mock/issues/1",
        status="opened",
        qa_result=None,
    )
    assert "Try-On" in text
    assert "internal note" not in text.lower()
    developer = developer_message(
        event_type="issue_assigned",
        product_name="Try-On",
        severity="high",
        customer_name="Northwind",
        reference="https://gitlab.mock/issues/1",
        title="title",
        description="Bracelet appears black",
    )
    assert "Northwind" in developer
    assert "Bracelet appears black" in developer


def test_conversation_requires_confirmation_before_submit():
    state = FlowState(ConversationState.IDLE, {})
    started = step(state, FlowInput("Report Bug", None, False, False), PRODUCTS)
    assert started.state == ConversationState.AWAITING_PRODUCT
    assert started.action == "none"
    invalid = step(started, FlowInput("project 4242", None, False, False), PRODUCTS)
    assert invalid.state == ConversationState.AWAITING_PRODUCT
    selected = step(started, FlowInput(None, "try-on", False, False), PRODUCTS)
    assert selected.state == ConversationState.AWAITING_IMAGE
    described_too_soon = step(selected, FlowInput("Bracelet appears black", None, False, False), PRODUCTS)
    assert described_too_soon.state == ConversationState.AWAITING_IMAGE
    with_image = step(selected, FlowInput(None, None, True, False), PRODUCTS)
    described = step(with_image, FlowInput("Bracelet appears black", None, False, False), PRODUCTS)
    severity = step(described, FlowInput(None, "sev_high", False, False), PRODUCTS)
    assert severity.state == ConversationState.AWAITING_CONFIRMATION
    declined = step(severity, FlowInput("no", None, False, False), PRODUCTS)
    assert declined.state == ConversationState.AWAITING_DESCRIPTION
    revised = step(declined, FlowInput("Bracelet appears black", None, False, False), PRODUCTS)
    confirmed_wait = step(revised, FlowInput("high", None, False, False), PRODUCTS)
    submitted = step(confirmed_wait, FlowInput("yes", None, False, False), PRODUCTS)
    assert submitted.action == "submit"
    cancelled = step(selected, FlowInput("cancel", None, False, False), PRODUCTS)
    assert cancelled.state == ConversationState.IDLE
    assert cancelled.action == "cancel"


def test_conversation_expiration_and_retry():
    waiting = FlowState(ConversationState.AWAITING_DESCRIPTION, {"product_slug": "try-on", "attachment_id": "a"})
    expired = step(waiting, FlowInput("Bracelet appears black", None, False, True), PRODUCTS)
    assert expired.state == ConversationState.FAILED
    restarted = step(waiting, FlowInput("Report Bug", None, False, True), PRODUCTS)
    assert restarted.state == ConversationState.AWAITING_PRODUCT
    failed = FlowState(
        ConversationState.FAILED,
        {"product_slug": "try-on", "attachment_id": "a", "description": "Bracelet appears black", "severity": "high"},
    )
    retry = step(failed, FlowInput("retry", None, False, False), PRODUCTS)
    assert retry.action == "submit"


def test_gitlab_and_whatsapp_payload_parsing():
    parsed = parse_gitlab_issue_event(
        {"object_kind": "issue", "project": {"id": 1001}, "object_attributes": {"id": 9, "iid": 3, "action": "reopen", "state": "opened"}}
    )
    assert parsed is not None
    assert parsed.issue_iid == "3"
    assert map_gitlab_status(action="reopen", state="opened", current_status="closed") == "reopened"
    assert map_gitlab_status(action="update", state="opened", current_status="reopened") == "reopened"
    assert ("bug_closed", "customer") in notification_events_for_gitlab("close", "closed", set())
    messages, statuses = parse_whatsapp_payload(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [{"from": "1555", "id": "m1", "type": "text", "text": {"body": "Report Bug"}}],
                                "statuses": [{"id": "wamid.1", "status": "failed", "recipient_id": "1555"}],
                            }
                        }
                    ]
                }
            ]
        }
    )
    assert messages[0].text == "Report Bug"
    assert statuses[0].status == "failed"
    assert parse_gitlab_issue_event({"object_kind": "note"}) is None


def test_groups_are_not_available_and_individual_send_is_used():
    assert group_messaging_available(groups_enabled=True, provider="cloud_api") is False
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    provider = CloudWhatsAppProvider(
        access_token="token",
        phone_number_id="phone",
        api_version="v21.0",
        groups_enabled=True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ValueError):
        provider.send_text("1203630@g.us", "hello")
    assert calls == []
    assert provider.send_text("15550001001", "hello") == "wamid.1"
    assert calls[0].url.path.endswith("/phone/messages")
    assert calls[0].headers["authorization"] == "Bearer token"


def test_gitlab_rest_does_not_retry_client_errors_and_recovers_timeouts():
    calls = {"post": 0, "get": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["post"] += 1
            return httpx.Response(400, json={"message": "no"})
        calls["get"] += 1
        return httpx.Response(200, json=[])

    provider = GitLabRestProvider("https://gitlab.example", "token", 1, client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(Exception):
        provider.create_issue(project_id="group/demo", title="t", description="d", labels=["bug"], assignee_id=None, marker="bug-report-id:1")
    assert calls["post"] == 1
    assert "PRIVATE-TOKEN" not in str(calls)

    def recovering(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "search" in str(request.url):
            if recovering.searches == 0:
                recovering.searches += 1
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[{"id": 5, "iid": 8, "project_id": 1001, "description": "bug-report-id:abc", "web_url": "https://gitlab.example/i/8", "state": "opened"}],
            )
        raise httpx.TimeoutException("timed out")

    recovering.searches = 0
    provider = GitLabRestProvider("https://gitlab.example", "token", 1, client=httpx.Client(transport=httpx.MockTransport(recovering)))
    issue = provider.create_issue(
        project_id="1001",
        title="t",
        description="<!-- bug-report-id:abc -->",
        labels=["bug", "whatsapp"],
        assignee_id="4",
        marker="bug-report-id:abc",
    )
    assert issue.iid == "8"
    assert issue.project_id == "1001"


def test_mock_gitlab_state_file_keeps_issue_ids(tmp_path):
    path = tmp_path / "gitlab.json"
    first = MockGitLabProvider(str(path))
    created = first.create_issue(
        project_id="1001",
        title="Clasp",
        description="<!-- bug-report-id:abc -->",
        labels=["bug"],
        assignee_id="77",
        marker="bug-report-id:abc",
    )
    assert created.iid == "1"
    restarted = MockGitLabProvider(str(path))
    assert restarted.get_issue(project_id="1001", issue_iid="1").id == created.id
    again = restarted.create_issue(
        project_id="1001",
        title="Same report",
        description="<!-- bug-report-id:abc -->",
        labels=["bug"],
        assignee_id="77",
        marker="bug-report-id:abc",
    )
    assert again.iid == "1"
    fresh = restarted.create_issue(
        project_id="1001",
        title="Next",
        description="<!-- bug-report-id:def -->",
        labels=["bug"],
        assignee_id=None,
        marker="bug-report-id:def",
    )
    assert fresh.iid == "2"
    restarted.ensure_sequence_at_least(9)
    skipped = restarted.create_issue(
        project_id="1001",
        title="Later",
        description="<!-- bug-report-id:ghi -->",
        labels=["bug"],
        assignee_id=None,
        marker="bug-report-id:ghi",
    )
    assert skipped.iid == "10"


def test_attachment_magic_and_path_traversal(tmp_path):
    assert detect_image_type(PNG) == "image/png"
    assert detect_image_type(b"GIF89a") is None
    store = AttachmentStore(str(tmp_path))
    with pytest.raises(Exception):
        store._resolve("../secrets.txt")
