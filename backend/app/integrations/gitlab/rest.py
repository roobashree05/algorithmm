import logging
import time
from urllib.parse import quote

import httpx

from app.core.errors import IntegrationError
from app.integrations.gitlab.base import GitLabIssue

logger = logging.getLogger(__name__)


class GitLabRestProvider:
    """Official GitLab REST API client. POST issue creation is not blindly retried."""

    def __init__(self, base_url: str, token: str, timeout: float, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.client = client or httpx.Client(timeout=timeout)

    def _project(self, project_id: str) -> str:
        return quote(str(project_id), safe="")

    def _request(self, method: str, url: str, *, retry_post: bool = False, **kwargs) -> dict:
        delay = 0.5
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.client.request(
                    method,
                    url,
                    headers={"PRIVATE-TOKEN": self.token},
                    timeout=self.timeout,
                    **kwargs,
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                if method == "GET" and attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise IntegrationError("GitLab request timed out", retryable=True) from exc
            except httpx.HTTPError as exc:
                raise IntegrationError("GitLab request failed", retryable=True) from exc

            if response.status_code == 429 or response.status_code >= 500:
                retryable_method = method == "GET" or retry_post
                if retryable_method and attempt < 2:
                    retry_after = response.headers.get("Retry-After")
                    time.sleep(float(retry_after) if retry_after else delay)
                    delay *= 2
                    continue
                raise IntegrationError(
                    f"GitLab request failed status={response.status_code}",
                    retryable=True,
                    status_code=response.status_code,
                )
            if response.status_code >= 400:
                raise IntegrationError(
                    f"GitLab request failed status={response.status_code}",
                    retryable=False,
                    status_code=response.status_code,
                )
            if response.status_code == 204 or not response.content:
                return {}
            return response.json()
        raise IntegrationError("GitLab request failed", retryable=True) from last_error

    def find_by_marker(self, project_id: str, marker: str) -> GitLabIssue | None:
        url = f"{self.base_url}/api/v4/projects/{self._project(project_id)}/search"
        payload = self._request("GET", url, params={"scope": "issues", "search": marker})
        if isinstance(payload, dict):
            return None
        for item in payload:
            description = item.get("description") or ""
            if marker in description:
                return self._to_issue(item)
        return None

    def create_issue(
        self,
        *,
        project_id: str,
        title: str,
        description: str,
        labels: list[str],
        assignee_id: str | None,
        marker: str,
    ) -> GitLabIssue:
        existing = self.find_by_marker(project_id, marker)
        if existing:
            return existing
        url = f"{self.base_url}/api/v4/projects/{self._project(project_id)}/issues"
        body: dict = {"title": title[:255], "description": description, "labels": ",".join(labels)}
        if assignee_id:
            body["assignee_ids"] = [int(assignee_id)]
        try:
            created = self._request("POST", url, json=body)
        except IntegrationError as exc:
            if exc.retryable:
                recovered = self.find_by_marker(project_id, marker)
                if recovered:
                    return recovered
            raise
        return self._to_issue(created)

    def upload_file(self, *, project_id: str, filename: str, content: bytes, content_type: str) -> str:
        url = f"{self.base_url}/api/v4/projects/{self._project(project_id)}/uploads"
        files = {"file": (filename, content, content_type)}
        payload = self._request("POST", url, files=files)
        markdown = payload.get("markdown")
        if not markdown:
            raise IntegrationError("GitLab upload did not return markdown", retryable=False)
        return str(markdown)

    def add_note(self, *, project_id: str, issue_iid: str, body: str) -> None:
        url = f"{self.base_url}/api/v4/projects/{self._project(project_id)}/issues/{issue_iid}/notes"
        self._request("POST", url, json={"body": body})

    def get_issue(self, *, project_id: str, issue_iid: str) -> GitLabIssue:
        url = f"{self.base_url}/api/v4/projects/{self._project(project_id)}/issues/{issue_iid}"
        return self._to_issue(self._request("GET", url))

    @staticmethod
    def _to_issue(payload: dict) -> GitLabIssue:
        project_id = payload.get("project_id")
        return GitLabIssue(
            id=str(payload["id"]),
            iid=str(payload["iid"]),
            web_url=str(payload.get("web_url") or ""),
            state=str(payload.get("state") or "opened"),
            project_id=str(project_id) if project_id is not None else None,
        )
