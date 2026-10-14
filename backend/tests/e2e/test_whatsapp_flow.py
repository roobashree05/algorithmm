from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.domain.models import BugReport, Conversation
from tests.helpers import PNG, WHATSAPP_SECRET, auth, deliver, post_whatsapp, session_of, whatsapp_payload, world


def _run_report(client, prefix="wamid.flow", wa_id="15550001001", description="Bracelet appears black", product="try-on"):
    client.app.state.whatsapp.register_media(f"{prefix}-media", PNG, "image/png")
    steps = [
        whatsapp_payload(f"{prefix}-1", wa_id, text="Report Bug"),
        whatsapp_payload(f"{prefix}-2", wa_id, interactive_id=product),
        whatsapp_payload(f"{prefix}-3", wa_id, image_id=f"{prefix}-media"),
        whatsapp_payload(f"{prefix}-4", wa_id, text=description),
        whatsapp_payload(f"{prefix}-5", wa_id, interactive_id="sev_high"),
        whatsapp_payload(f"{prefix}-6", wa_id, text="Yes"),
    ]
    responses = [post_whatsapp(client, payload) for payload in steps]
    return responses


def test_whatsapp_report_creates_gitlab_issue_and_confirmation(client):
    world(client)
    responses = _run_report(client)
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    assert len(client.app.state.gitlab.created) == 1
    created = client.app.state.gitlab.created[0]
    assert created["project_id"] == "1001"
    assert "4242" not in created["description"]
    deliver(client)
    bodies = [item["body"] for item in client.app.state.whatsapp.sent if item["to"] == "15550001001"]
    assert any("Select the product" in body for body in bodies)
    assert any("Bug report submitted" in body for body in bodies)
    assert any("https://gitlab.mock/projects/1001/-/issues/1" in body for body in bodies)
    listed = client.get("/api/v1/bugs", headers=auth("customer-token")).json()
    assert listed["total"] == 1
    assert listed["items"][0]["source"] == "whatsapp"
    hidden = client.get(f"/api/v1/bugs/{listed['items'][0]['id']}", headers=auth("customer-2-token"))
    assert hidden.status_code == 404


def test_invalid_product_cancel_resume_and_duplicate_webhook(client):
    world(client)
    start = post_whatsapp(client, whatsapp_payload("wamid.start", text="Report Bug"))
    assert start.status_code == 200
    invalid = post_whatsapp(client, whatsapp_payload("wamid.bad-product", text="project 4242"))
    assert invalid.status_code == 200
    assert client.app.state.gitlab.created == []
    duplicate = post_whatsapp(client, whatsapp_payload("wamid.bad-product", text="project 4242"))
    assert duplicate.json()["status"] == "duplicate"
    selected = post_whatsapp(client, whatsapp_payload("wamid.product", interactive_id="try-on"))
    assert selected.status_code == 200
    cancelled = post_whatsapp(client, whatsapp_payload("wamid.cancel", text="cancel"))
    assert cancelled.status_code == 200
    assert client.app.state.gitlab.created == []
    deliver(client)
    assert any("cancelled" in item["body"].lower() for item in client.app.state.whatsapp.sent)

    post_whatsapp(client, whatsapp_payload("wamid.restart", text="Report Bug"))
    post_whatsapp(client, whatsapp_payload("wamid.restart-product", text="Try-On"))
    session = session_of(client)
    conversation = session.scalar(select(Conversation).where(Conversation.state == "AWAITING_IMAGE"))
    assert conversation is not None
    conversation.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    session.commit()
    session.close()
    expired = post_whatsapp(client, whatsapp_payload("wamid.expired", text="Bracelet appears black"))
    assert expired.status_code == 200
    assert client.app.state.gitlab.created == []
    deliver(client)
    assert any("expired" in item["body"].lower() for item in client.app.state.whatsapp.sent)


def test_conversation_resumes_and_gitlab_failure_can_retry(client):
    world(client)
    client.app.state.whatsapp.register_media("media-resume", PNG, "image/png")
    assert post_whatsapp(client, whatsapp_payload("wamid.r1", text="report bug")).status_code == 200
    assert post_whatsapp(client, whatsapp_payload("wamid.r2", interactive_id="try-on")).status_code == 200
    assert post_whatsapp(client, whatsapp_payload("wamid.r3", image_id="media-resume")).status_code == 200
    session = session_of(client)
    state = session.scalar(select(Conversation.state).where(Conversation.wa_id == "15550001001").order_by(Conversation.created_at.desc()))
    session.close()
    assert state == "AWAITING_DESCRIPTION"
    assert post_whatsapp(client, whatsapp_payload("wamid.r4", text="Bracelet appears black")).status_code == 200
    assert post_whatsapp(client, whatsapp_payload("wamid.r5", text="high")).status_code == 200
    client.app.state.gitlab.fail_remaining = 1
    failed = post_whatsapp(client, whatsapp_payload("wamid.r6", text="yes"))
    assert failed.status_code == 200, failed.text
    assert client.app.state.gitlab.created == []
    retried = post_whatsapp(client, whatsapp_payload("wamid.r7", text="retry"))
    assert retried.status_code == 200, retried.text
    assert len(client.app.state.gitlab.created) == 1
    session = session_of(client)
    bugs = list(session.scalars(select(BugReport)).all())
    session.close()
    assert len(bugs) == 1
    assert bugs[0].gitlab_issue_iid == "1"


def test_concurrent_conversations_stay_isolated(client):
    world(client)
    client.app.state.whatsapp.register_media("media-a", PNG, "image/png")
    client.app.state.whatsapp.register_media("media-b", PNG, "image/png")
    post_whatsapp(client, whatsapp_payload("a1", "15550001001", text="Report Bug"))
    post_whatsapp(client, whatsapp_payload("b1", "15550001002", text="Report Bug"))
    post_whatsapp(client, whatsapp_payload("a2", "15550001001", interactive_id="try-on"))
    post_whatsapp(client, whatsapp_payload("b2", "15550001002", interactive_id="inventory"))
    post_whatsapp(client, whatsapp_payload("a3", "15550001001", image_id="media-a"))
    post_whatsapp(client, whatsapp_payload("b3", "15550001002", image_id="media-b"))
    post_whatsapp(client, whatsapp_payload("a4", "15550001001", text="Bracelet appears black"))
    post_whatsapp(client, whatsapp_payload("b4", "15550001002", text="Stock count is wrong"))
    post_whatsapp(client, whatsapp_payload("a5", "15550001001", text="high"))
    post_whatsapp(client, whatsapp_payload("b5", "15550001002", text="low"))
    post_whatsapp(client, whatsapp_payload("a6", "15550001001", text="yes"))
    post_whatsapp(client, whatsapp_payload("b6", "15550001002", text="yes"))
    assert len(client.app.state.gitlab.created) == 2
    projects = {item["project_id"] for item in client.app.state.gitlab.created}
    assert projects == {"1001", "1002"}
    northwind = client.get("/api/v1/bugs", headers=auth("customer-token")).json()
    contoso = client.get("/api/v1/bugs", headers=auth("customer-2-token")).json()
    assert northwind["total"] == 1
    assert contoso["total"] == 1
    assert northwind["items"][0]["customer_code"] == "northwind"
    assert contoso["items"][0]["customer_code"] == "contoso"


def test_invalid_whatsapp_webhook_and_unknown_sender(client):
    world(client)
    bad_signature = post_whatsapp(client, whatsapp_payload("wamid.bad", text="Report Bug"), secret="wrong")
    assert bad_signature.status_code == 401
    missing = client.post("/webhooks/whatsapp", json={"entry": []})
    assert missing.status_code == 401
    invalid = post_whatsapp(client, {"not": "a whatsapp payload"})
    assert invalid.status_code == 200
    assert invalid.json()["status"] == "ok"
    verify = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "123"})
    assert verify.status_code == 401
    ok = client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": WHATSAPP_SECRET and "test-verify-token", "hub.challenge": "456"},
    )
    assert ok.status_code == 200
    assert ok.text == "456"
    unknown = post_whatsapp(client, whatsapp_payload("wamid.unknown", "19998887777", text="Report Bug"))
    assert unknown.status_code == 200
    deliver(client)
    assert any("not authorized" in item["body"].lower() for item in client.app.state.whatsapp.sent)
    assert client.app.state.gitlab.created == []
