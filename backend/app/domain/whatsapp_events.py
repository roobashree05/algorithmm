from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedInbound:
    message_id: str
    wa_id: str
    text: str | None
    interactive_id: str | None
    image_media_id: str | None
    image_mime: str | None
    profile_name: str | None


@dataclass(frozen=True)
class ParsedStatus:
    message_id: str
    status: str
    recipient_id: str | None


def parse_whatsapp_payload(payload: dict) -> tuple[list[ParsedInbound], list[ParsedStatus]]:
    messages: list[ParsedInbound] = []
    statuses: list[ParsedStatus] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            value = (change or {}).get("value") or {}
            names = {}
            for contact in value.get("contacts") or []:
                if isinstance(contact, dict) and contact.get("wa_id"):
                    profile = contact.get("profile") or {}
                    names[str(contact["wa_id"])] = profile.get("name")
            for raw in value.get("messages") or []:
                parsed = _parse_message(raw, names)
                if parsed:
                    messages.append(parsed)
            for raw in value.get("statuses") or []:
                if isinstance(raw, dict) and raw.get("id") and raw.get("status"):
                    statuses.append(
                        ParsedStatus(
                            message_id=str(raw["id"]),
                            status=str(raw["status"]),
                            recipient_id=str(raw["recipient_id"]) if raw.get("recipient_id") else None,
                        )
                    )
    return messages, statuses


def _parse_message(raw: dict, names: dict[str, str | None]) -> ParsedInbound | None:
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("from"):
        return None
    text = None
    interactive_id = None
    image_media_id = None
    image_mime = None
    kind = raw.get("type")
    if kind == "text":
        body = (raw.get("text") or {}).get("body")
        text = str(body) if body is not None else None
    elif kind == "interactive":
        interactive = raw.get("interactive") or {}
        reply = interactive.get("list_reply") or interactive.get("button_reply") or {}
        interactive_id = str(reply.get("id")) if reply.get("id") else None
        text = str(reply.get("title")) if reply.get("title") else None
    elif kind == "image":
        image = raw.get("image") or {}
        image_media_id = str(image["id"]) if image.get("id") else None
        image_mime = str(image.get("mime_type") or "")
        if image.get("caption"):
            text = str(image["caption"])
    elif kind == "button":
        button = raw.get("button") or {}
        interactive_id = str(button.get("payload") or "") or None
        text = str(button.get("text") or "") or None
    wa_id = str(raw["from"])
    return ParsedInbound(
        message_id=str(raw["id"]),
        wa_id=wa_id,
        text=text,
        interactive_id=interactive_id,
        image_media_id=image_media_id,
        image_mime=image_mime,
        profile_name=names.get(wa_id),
    )
