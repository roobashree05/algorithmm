import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

from sqlalchemy import select

from app.core.security import hash_token
from app.domain.models import Customer, CustomerUser, DeveloperTeam, Product, TeamMember, User
from app.workers.outbox_worker import process_outbox_batch

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
WHATSAPP_SECRET = "test-whatsapp-secret"
GITLAB_SECRET = "test-gitlab-secret"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def sign_body(body: bytes, secret: str = WHATSAPP_SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def deliver(client, now=None) -> int:
    return process_outbox_batch(
        client.app.state.session_factory,
        client.app.state.whatsapp,
        client.app.state.settings,
        now,
    )


def build_world(client) -> SimpleNamespace:
    session = client.app.state.session_factory()
    northwind = Customer(name="Northwind Traders", code="northwind", is_active=True)
    contoso = Customer(name="Contoso Ltd", code="contoso", is_active=True)
    try_on_team = DeveloperTeam(name="Try-On Team", slug="try-on")
    inventory_team = DeveloperTeam(name="Inventory Team", slug="inventory")
    session.add_all([northwind, contoso, try_on_team, inventory_team])
    session.flush()
    products = {
        "try-on": Product(
            name="Try-On",
            slug="try-on",
            gitlab_project_id="1001",
            gitlab_project_path="demo/try-on",
            developer_team_id=try_on_team.id,
            default_labels=["bug", "whatsapp", "try-on", "customer-reported"],
            default_assignee_gitlab_id="77",
            is_active=True,
        ),
        "inventory": Product(
            name="Inventory",
            slug="inventory",
            gitlab_project_id="1002",
            gitlab_project_path="demo/inventory",
            developer_team_id=inventory_team.id,
            default_labels=["bug", "whatsapp", "inventory", "customer-reported"],
            is_active=True,
        ),
    }
    session.add_all(products.values())
    users = {
        "admin": _user(session, "admin@local.test", "Admin", "ADMIN", "+15550001000", "admin-token"),
        "customer": _user(session, "customer@local.test", "Northwind Reporter", "CUSTOMER", "+15550001001", "customer-token"),
        "customer2": _user(session, "contoso@local.test", "Contoso Reporter", "CUSTOMER", "+15550001002", "customer-2-token"),
        "tester": _user(session, "tester@local.test", "Northwind Tester", "TESTER", "+15550001003", "tester-token"),
        "developer": _user(session, "developer@local.test", "Try-On Developer", "DEVELOPER", "+15550001004", "developer-token"),
        "developer2": _user(session, "inventory@local.test", "Inventory Developer", "DEVELOPER", "+15550001006", "developer-2-token"),
        "management": _user(session, "management@local.test", "Manager", "MANAGEMENT", "+15550001005", "management-token"),
    }
    session.add_all(
        [
            CustomerUser(customer_id=northwind.id, user_id=users["customer"].id),
            CustomerUser(customer_id=contoso.id, user_id=users["customer2"].id),
            CustomerUser(customer_id=northwind.id, user_id=users["tester"].id),
            TeamMember(team_id=try_on_team.id, user_id=users["developer"].id),
            TeamMember(team_id=inventory_team.id, user_id=users["developer2"].id),
        ]
    )
    session.commit()
    session.close()


def _user(session, email, name, role, phone, token):
    user = User(
        email=email,
        display_name=name,
        role=role,
        phone_e164=phone,
        token_hash=hash_token(token),
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def world(client) -> SimpleNamespace:
    build_world(client)
    return SimpleNamespace(
        client=client,
        tokens={
            "admin": "admin-token",
            "customer": "customer-token",
            "customer2": "customer-2-token",
            "tester": "tester-token",
            "developer": "developer-token",
            "developer2": "developer-2-token",
            "management": "management-token",
        },
    )


def bug_payload(**overrides):
    payload = {
        "product_slug": "try-on",
        "description": "Bracelet appears black",
        "severity": "high",
    }
    payload.update(overrides)
    return payload


def whatsapp_payload(message_id: str, wa_id: str = "15550001001", *, text=None, interactive_id=None, image_id=None, mime="image/png"):
    message: dict = {"from": wa_id, "id": message_id, "timestamp": "1710000000"}
    if image_id:
        message["type"] = "image"
        message["image"] = {"id": image_id, "mime_type": mime}
    elif interactive_id:
        message["type"] = "interactive"
        message["interactive"] = {"type": "list_reply", "list_reply": {"id": interactive_id, "title": interactive_id}}
    else:
        message["type"] = "text"
        message["text"] = {"body": text or ""}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "BUSINESS",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"profile": {"name": "Reporter"}, "wa_id": wa_id}],
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }


def post_whatsapp(client, payload: dict, secret: str = WHATSAPP_SECRET):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={"content-type": "application/json", "x-hub-signature-256": sign_body(body, secret)},
    )


def post_gitlab(client, payload: dict, *, token: str = GITLAB_SECRET, event_id: str = "event-1"):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/gitlab",
        content=body,
        headers={"content-type": "application/json", "x-gitlab-token": token, "x-gitlab-event-uuid": event_id},
    )


def gitlab_close(project_id: str, iid: str, action: str = "close", state: str = "closed") -> dict:
    return {
        "object_kind": "issue",
        "project": {"id": int(project_id) if project_id.isdigit() else project_id, "path_with_namespace": "demo/try-on"},
        "object_attributes": {
            "id": 301,
            "iid": int(iid) if str(iid).isdigit() else iid,
            "title": "issue",
            "state": state,
            "action": action,
            "url": f"https://gitlab.mock/projects/{project_id}/-/issues/{iid}",
        },
        "changes": {},
    }


def sent_to(client, phone_digits: str) -> list[dict]:
    return [item for item in client.app.state.whatsapp.sent if item["to"] == phone_digits]


def session_of(client):
    return client.app.state.session_factory()


def reload_user(client, email: str):
    session = session_of(client)
    try:
        return session.scalar(select(User).where(User.email == email))
    finally:
        session.close()
