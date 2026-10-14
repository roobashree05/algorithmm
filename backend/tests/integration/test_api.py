from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.domain.models import NotificationOutbox, NotificationRule, Product, User
from tests.helpers import GITLAB_SECRET, PNG, auth, bug_payload, deliver, gitlab_close, post_gitlab, sent_to, session_of, world


def test_health_ready_and_openapi(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["status"] == "ready"
    schema = client.get("/openapi.json").json()
    assert "/api/v1/bugs" in schema["paths"]
    assert "/webhooks/gitlab" in schema["paths"]
    assert "/webhooks/whatsapp" in schema["paths"]
    assert client.get("/api/v1/bugs").status_code == 401
    assert "X-Correlation-ID" in client.get("/api/v1/bugs").headers


def test_valid_bug_submission_creates_one_gitlab_issue(client):
    data = world(client)
    response = client.post("/api/v1/bugs", json=bug_payload(idempotency_key="report-1"), headers=auth(data.tokens["customer"]))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "opened"
    assert body["product_slug"] == "try-on"
    assert body["customer_code"] == "northwind"
    assert body["gitlab_issue_iid"] == "1"
    assert body["gitlab_project_id"] is None
    again = client.post("/api/v1/bugs", json=bug_payload(idempotency_key="report-1"), headers=auth(data.tokens["customer"]))
    assert again.status_code == 201
    assert again.json()["id"] == body["id"]
    assert len(client.app.state.gitlab.created) == 1
    created = client.app.state.gitlab.created[0]
    assert created["labels"] == ["bug", "whatsapp", "try-on", "customer-reported"]
    assert created["assignee_id"] == "77"
    assert "bug-report-id:" in created["description"]
    assert "Bracelet appears black" in created["description"]


def test_invalid_product_and_untrusted_project_id_are_rejected(client):
    data = world(client)
    headers = auth(data.tokens["customer"])
    invalid = client.post("/api/v1/bugs", json=bug_payload(product_slug="missing"), headers=headers)
    assert invalid.status_code == 422
    extra = client.post("/api/v1/bugs", json={**bug_payload(), "gitlab_project_id": "9999"}, headers=headers)
    assert extra.status_code == 422
    forbidden_customer = client.post("/api/v1/bugs", json=bug_payload(customer_code="contoso"), headers=headers)
    assert forbidden_customer.status_code == 403
    developer = client.post("/api/v1/bugs", json=bug_payload(), headers=auth(data.tokens["developer"]))
    assert developer.status_code == 403
    assert client.app.state.gitlab.created == []


def test_multipart_submit_without_screenshot_and_mock_restart(client):
    from app.integrations.gitlab.mock import MockGitLabProvider

    data = world(client)
    headers = auth(data.tokens["customer"])
    fields = {"product_slug": "try-on", "description": "Clasp does not close", "severity": "high"}
    first = client.post("/api/v1/bugs", data=fields, files={"placeholder": (None, "")}, headers=headers)
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "opened"
    assert first.json()["gitlab_issue_iid"] == "1"
    assert first.json()["attachments"] == []
    client.app.state.gitlab = MockGitLabProvider()
    second = client.post("/api/v1/bugs", data=fields, files={"placeholder": (None, "")}, headers=headers)
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "opened"
    assert second.json()["gitlab_issue_iid"] == "2"
    assert second.json()["id"] != first.json()["id"]
    listed = client.get("/api/v1/bugs", headers=headers).json()
    assert {item["gitlab_issue_iid"] for item in listed["items"]} == {"1", "2"}


def test_attachment_validation(client):
    data = world(client)
    headers = auth(data.tokens["customer"])
    fields = {"product_slug": "try-on", "description": "Bracelet appears black", "severity": "high"}
    gif = client.post("/api/v1/bugs", data=fields, files={"screenshot": ("shot.gif", b"GIF89a" + b"0" * 20, "image/gif")}, headers=headers)
    assert gif.status_code == 422
    huge = b"\xff\xd8\xff" + b"0" * 2000
    oversized = client.post(
        "/api/v1/bugs",
        data=fields,
        files={"screenshot": ("shot.jpg", huge, "image/jpeg")},
        headers=headers,
    )
    assert oversized.status_code == 422
    mismatch = client.post(
        "/api/v1/bugs",
        data=fields,
        files={"screenshot": ("shot.jpg", PNG, "image/jpeg")},
        headers=headers,
    )
    assert mismatch.status_code == 422
    valid = client.post(
        "/api/v1/bugs",
        data=fields,
        files={"screenshot": ("shot.png", PNG, "image/png")},
        headers=headers,
    )
    assert valid.status_code == 201, valid.text
    assert valid.json()["attachments"][0]["content_type"] == "image/png"
    assert client.app.state.gitlab.uploads[0]["content_type"] == "image/png"
    assert "Screenshot" in client.app.state.gitlab.created[0]["description"]


def test_cross_customer_isolation_and_role_visibility(client):
    data = world(client)
    created = client.post("/api/v1/bugs", json=bug_payload(), headers=auth(data.tokens["customer"])).json()
    other = client.post(
        "/api/v1/bugs",
        json=bug_payload(product_slug="inventory", description="Stock count is wrong"),
        headers=auth(data.tokens["customer2"]),
    ).json()
    customer_list = client.get("/api/v1/bugs", headers=auth(data.tokens["customer"])).json()
    assert [item["id"] for item in customer_list["items"]] == [created["id"]]
    hidden = client.get(f"/api/v1/bugs/{other['id']}", headers=auth(data.tokens["customer"]))
    assert hidden.status_code == 404
    tester_list = client.get("/api/v1/bugs", headers=auth(data.tokens["tester"])).json()
    assert [item["id"] for item in tester_list["items"]] == [created["id"]]
    developer_list = client.get("/api/v1/bugs", headers=auth(data.tokens["developer"])).json()
    assert [item["id"] for item in developer_list["items"]] == [created["id"]]
    assert developer_list["items"][0]["gitlab_project_id"] == "1001"
    other_developer = client.get(f"/api/v1/bugs/{created['id']}", headers=auth(data.tokens["developer2"]))
    assert other_developer.status_code == 404
    management = client.get("/api/v1/bugs", headers=auth(data.tokens["management"])).json()
    assert {item["id"] for item in management["items"]} == {created["id"], other["id"]}
    summaries = client.get("/api/v1/summaries/products", headers=auth(data.tokens["management"]))
    assert summaries.status_code == 200
    assert client.get("/api/v1/summaries/products", headers=auth(data.tokens["customer"])).status_code == 403


def test_admin_endpoints_require_admin_and_configure_mapping(client):
    data = world(client)
    denied = client.post(
        "/api/v1/admin/products",
        json={
            "name": "Design Studio",
            "slug": "design-studio",
            "gitlab_project_id": "1004",
            "gitlab_project_path": "demo/design-studio",
            "developer_team_slug": "try-on",
            "default_labels": ["bug", "whatsapp", "design-studio", "customer-reported"],
        },
        headers=auth(data.tokens["customer"]),
    )
    assert denied.status_code == 403
    created = client.post(
        "/api/v1/admin/developer-teams",
        json={"name": "Design Team", "slug": "design-studio", "member_emails": ["developer@local.test"]},
        headers=auth(data.tokens["admin"]),
    )
    assert created.status_code == 201, created.text
    product = client.post(
        "/api/v1/admin/products",
        json={
            "name": "Design Studio",
            "slug": "design-studio",
            "gitlab_project_id": "1004",
            "gitlab_project_path": "demo/design-studio",
            "developer_team_slug": "design-studio",
            "default_labels": ["bug", "whatsapp", "design-studio", "customer-reported"],
        },
        headers=auth(data.tokens["admin"]),
    )
    assert product.status_code == 201, product.text
    mapped = client.post(
        "/api/v1/admin/user-mappings",
        json={
            "email": "new.customer@local.test",
            "display_name": "New Customer",
            "role": "CUSTOMER",
            "phone_e164": "+15550001999",
            "customer_code": "northwind",
            "api_token": "new-customer-token",
        },
        headers=auth(data.tokens["admin"]),
    )
    assert mapped.status_code == 201, mapped.text
    assert mapped.json()["api_token"] == "new-customer-token"
    submitted = client.post("/api/v1/bugs", json=bug_payload(), headers=auth("new-customer-token"))
    assert submitted.status_code == 201


def test_notification_routing_failure_and_retry_do_not_duplicate_issues(client):
    data = world(client)
    created = client.post("/api/v1/bugs", json=bug_payload(), headers=auth(data.tokens["customer"]))
    assert created.status_code == 201, created.text
    bug_id = created.json()["id"]
    client.app.state.whatsapp.fail_remaining = 20
    assert deliver(client) == 0
    session = session_of(client)
    rows = list(session.scalars(select(NotificationOutbox)).all())
    assert rows
    assert all(row.attempts == 1 for row in rows)
    assert all(row.status == "pending" for row in rows)
    assert len(client.app.state.gitlab.created) == 1
    client.app.state.whatsapp.fail_remaining = 0
    later = datetime.now(timezone.utc) + timedelta(seconds=5)
    assert deliver(client, now=later) == len(rows)
    session.expire_all()
    rows = list(session.scalars(select(NotificationOutbox)).all())
    session.close()
    assert {row.status for row in rows} == {"sent"}
    assert len(client.app.state.gitlab.created) == 1
    phones = {item["to"] for item in client.app.state.whatsapp.sent}
    assert "15550001001" in phones
    assert "15550001004" in phones
    assert "15550001005" in phones
    assert "15550001002" not in phones
    customer_text = " ".join(item["body"] for item in sent_to(client, "15550001001"))
    assert "submitted" in customer_text
    assert "Contoso" not in customer_text
    history = client.get(f"/api/v1/bugs/{bug_id}/history", headers=auth(data.tokens["customer"])).json()
    assert any(event["action"] == "bug_submitted" for event in history["events"])
    assert all(event["action"] != "internal_note" for event in history["events"])


def test_disabled_rule_and_revoked_recipient_are_not_sent(client):
    data = world(client)
    session = session_of(client)
    product_id = session.scalar(select(Product.id).where(Product.slug == "try-on"))
    session.add(NotificationRule(event_type="bug_submitted", audience="management", product_id=product_id, enabled=False))
    session.commit()
    session.close()
    assert client.post("/api/v1/bugs", json=bug_payload(), headers=auth(data.tokens["customer"])).status_code == 201
    session = session_of(client)
    developer = session.scalar(select(User).where(User.email == "developer@local.test"))
    developer.is_active = False
    session.commit()
    session.close()
    deliver(client)
    phones = {item["to"] for item in client.app.state.whatsapp.sent}
    assert "15550001005" not in phones
    assert "15550001004" not in phones
    assert "15550001001" in phones


def test_gitlab_webhook_updates_status_and_duplicate_delivery_is_ignored(client):
    data = world(client)
    created = client.post("/api/v1/bugs", json=bug_payload(), headers=auth(data.tokens["customer"])).json()
    close_payload = gitlab_close("1001", created["gitlab_issue_iid"])
    bad = post_gitlab(client, close_payload, token="wrong", event_id="event-close")
    assert bad.status_code == 401
    invalid = client.post("/webhooks/gitlab", content=b"{", headers={"x-gitlab-token": GITLAB_SECRET})
    assert invalid.status_code == 422
    first = post_gitlab(client, close_payload, event_id="event-close")
    assert first.status_code == 200, first.text
    assert first.json()["bug_status"] == "closed"
    second = post_gitlab(client, close_payload, event_id="event-close")
    assert second.json()["status"] == "duplicate"
    listed = client.get(f"/api/v1/bugs/{created['id']}", headers=auth(data.tokens["customer"])).json()
    assert listed["status"] == "closed"
    deliver(client)
    assert any("closed" in item["body"] for item in sent_to(client, "15550001001"))
    reopen = gitlab_close("1001", created["gitlab_issue_iid"], action="reopen", state="opened")
    assert post_gitlab(client, reopen, event_id="event-reopen").json()["bug_status"] == "reopened"
    assert len(client.app.state.gitlab.created) == 1


def test_qa_and_internal_notes_follow_authorization(client):
    data = world(client)
    created = client.post("/api/v1/bugs", json=bug_payload(), headers=auth(data.tokens["tester"])).json()
    note = client.post(
        f"/api/v1/bugs/{created['id']}/notes",
        json={"body": "Only the team should see this", "visibility": "internal"},
        headers=auth(data.tokens["developer"]),
    )
    assert note.status_code == 204, note.text
    customer_history = client.get(f"/api/v1/bugs/{created['id']}/history", headers=auth(data.tokens["customer"])).json()
    assert all(event["action"] != "internal_note" for event in customer_history["events"])
    denied = client.post(
        f"/api/v1/bugs/{created['id']}/qa",
        json={"result": "failed", "comment": "Still reproduces"},
        headers=auth(data.tokens["developer"]),
    )
    assert denied.status_code == 403
    qa = client.post(
        f"/api/v1/bugs/{created['id']}/qa",
        json={"result": "failed", "comment": "Still reproduces"},
        headers=auth(data.tokens["tester"]),
    )
    assert qa.status_code == 204, qa.text
    assert "QA result" in client.app.state.gitlab.notes[-1]["body"]
    deliver(client)
    assert any("QA result" in item["body"] for item in sent_to(client, "15550001003"))


def test_rate_limit_returns_consistent_error(settings, tmp_path):
    settings.rate_limit_per_minute = 2
    from app.main import create_app
    from fastapi.testclient import TestClient

    application = create_app(settings)
    with TestClient(application) as limited:
        assert limited.get("/api/v1/bugs").status_code == 401
        assert limited.get("/api/v1/bugs").status_code == 401
        blocked = limited.get("/api/v1/bugs")
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "rate_limited"
