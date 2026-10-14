import logging
import time

import httpx

from app.core.errors import IntegrationError
from app.integrations.whatsapp.capabilities import assert_individual_destination, group_messaging_available

logger = logging.getLogger(__name__)


class CloudWhatsAppProvider:
    """WhatsApp Business Platform Cloud API client for individual chats."""

    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        api_version: str,
        groups_enabled: bool,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.api_version = api_version
        self.groups_supported = group_messaging_available(groups_enabled=groups_enabled, provider="cloud_api")
        self.timeout = timeout
        self.client = client or httpx.Client(timeout=timeout)

    def send_text(self, to: str, body: str) -> str:
        payload = {
            "messaging_product": "whatsapp",
            "to": assert_individual_destination(to),
            "type": "text",
            "text": {"preview_url": False, "body": body[:4096]},
        }
        return self._send(payload)

    def send_list(self, to: str, body: str, options: list[dict]) -> str:
        rows = [{"id": item["id"][:200], "title": item["title"][:24]} for item in options[:10]]
        payload = {
            "messaging_product": "whatsapp",
            "to": assert_individual_destination(to),
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body[:1024]},
                "action": {"button": "Choose", "sections": [{"title": "Options", "rows": rows}]},
            },
        }
        return self._send(payload)

    def send_buttons(self, to: str, body: str, options: list[dict]) -> str:
        buttons = [
            {"type": "reply", "reply": {"id": item["id"][:256], "title": item["title"][:20]}}
            for item in options[:3]
        ]
        payload = {
            "messaging_product": "whatsapp",
            "to": assert_individual_destination(to),
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body[:1024]},
                "action": {"buttons": buttons},
            },
        }
        return self._send(payload)

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        meta = self._request("GET", f"https://graph.facebook.com/{self.api_version}/{media_id}", retry=True)
        url = meta.get("url")
        if not url:
            raise IntegrationError("WhatsApp media metadata did not include a URL", retryable=False)
        response = self._raw("GET", url, retry=True)
        content_type = response.headers.get("content-type", "application/octet-stream").split(";")[0]
        return response.content, content_type

    def _send(self, payload: dict) -> str:
        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        result = self._request("POST", url, json=payload, retry=False)
        messages = result.get("messages") or []
        if not messages or not messages[0].get("id"):
            raise IntegrationError("WhatsApp send did not return a message id", retryable=True)
        return str(messages[0]["id"])

    def _request(self, method: str, url: str, *, retry: bool, **kwargs) -> dict:
        response = self._raw(method, url, retry=retry, **kwargs)
        if not response.content:
            return {}
        return response.json()

    def _raw(self, method: str, url: str, *, retry: bool, **kwargs) -> httpx.Response:
        attempts = 3 if retry else 1
        delay = 0.5
        for attempt in range(attempts):
            try:
                response = self.client.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    timeout=self.timeout,
                    **kwargs,
                )
            except httpx.TimeoutException as exc:
                if retry and attempt < attempts - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise IntegrationError("WhatsApp request timed out", retryable=True) from exc
            except httpx.HTTPError as exc:
                raise IntegrationError("WhatsApp request failed", retryable=True) from exc
            if response.status_code == 429 or response.status_code >= 500:
                if retry and attempt < attempts - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise IntegrationError(
                    f"WhatsApp request failed status={response.status_code}",
                    retryable=True,
                    status_code=response.status_code,
                )
            if response.status_code >= 400:
                logger.info("whatsapp request rejected", extra={"status_code": response.status_code})
                raise IntegrationError(
                    f"WhatsApp request failed status={response.status_code}",
                    retryable=False,
                    status_code=response.status_code,
                )
            return response
        raise IntegrationError("WhatsApp request failed", retryable=True)
