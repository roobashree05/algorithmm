import uuid

from app.core.errors import IntegrationError
from app.integrations.whatsapp.capabilities import assert_individual_destination


class MockWhatsAppProvider:
    """Simulates Cloud API sends, media download, and delivery without credentials."""

    def __init__(self):
        self.sent: list[dict] = []
        self.media: dict[str, tuple[bytes, str]] = {}
        self.fail_remaining = 0

    def register_media(self, media_id: str, content: bytes, content_type: str) -> None:
        self.media[media_id] = (content, content_type)

    def send_text(self, to: str, body: str) -> str:
        return self._send(to, "text", body, [])

    def send_list(self, to: str, body: str, options: list[dict]) -> str:
        return self._send(to, "list", body, options)

    def send_buttons(self, to: str, body: str, options: list[dict]) -> str:
        return self._send(to, "buttons", body, options)

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        if media_id not in self.media:
            raise IntegrationError("mock media not found", retryable=False, status_code=404)
        return self.media[media_id]

    def _send(self, to: str, kind: str, body: str, options: list[dict]) -> str:
        assert_individual_destination(to)
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            raise IntegrationError("mock whatsapp send failed", retryable=True, status_code=503)
        message_id = f"wamid.mock.{uuid.uuid4().hex}"
        self.sent.append({"id": message_id, "to": to, "kind": kind, "body": body, "options": options})
        return message_id
