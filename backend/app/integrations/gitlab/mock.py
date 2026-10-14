import json
import threading
from pathlib import Path

from app.core.errors import IntegrationError
from app.integrations.gitlab.base import GitLabIssue


class MockGitLabProvider:
    """In-memory GitLab stand-in for local tests. Does not call the network.

    An optional state file keeps issue ids across process restarts. Without it,
    a new process starts the sequence at zero while PostgreSQL still stores the
    previous ids.
    """

    def __init__(self, state_path: str | None = None):
        self.state_path = state_path or None
        self.issues: dict[tuple[str, str], GitLabIssue] = {}
        self.descriptions: dict[tuple[str, str], str] = {}
        self.created: list[dict] = []
        self.uploads: list[dict] = []
        self.notes: list[dict] = []
        self.fail_remaining = 0
        self._sequence = 0
        self._lock = threading.RLock()
        self._load()

    def ensure_sequence_at_least(self, minimum: int) -> None:
        with self._lock:
            if minimum > self._sequence:
                self._sequence = minimum
                self._save()

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
        with self._lock:
            if self.fail_remaining > 0:
                self.fail_remaining -= 1
                raise IntegrationError("mock gitlab unavailable", retryable=True, status_code=503)
            existing = self.find_by_marker(project_id, marker)
            if existing:
                return existing
            return self._allocate(
                project_id=project_id,
                title=title,
                description=description,
                labels=labels,
                assignee_id=assignee_id,
                marker=marker,
            )

    def reissue(
        self,
        *,
        project_id: str,
        title: str,
        description: str,
        labels: list[str],
        assignee_id: str | None,
        marker: str,
    ) -> GitLabIssue:
        """Drop the issue stored for this marker and allocate a new iid."""
        with self._lock:
            self._drop_marker(project_id, marker)
            return self._allocate(
                project_id=project_id,
                title=title,
                description=description,
                labels=labels,
                assignee_id=assignee_id,
                marker=marker,
            )

    def find_by_marker(self, project_id: str, marker: str) -> GitLabIssue | None:
        for (stored_project, _iid), description in self.descriptions.items():
            if stored_project == str(project_id) and marker in description:
                return self.issues[(stored_project, _iid)]
        return None

    def upload_file(self, *, project_id: str, filename: str, content: bytes, content_type: str) -> str:
        with self._lock:
            if self.fail_remaining > 0:
                self.fail_remaining -= 1
                raise IntegrationError("mock gitlab upload failed", retryable=True, status_code=503)
            self.uploads.append(
                {"project_id": str(project_id), "filename": filename, "size": len(content), "content_type": content_type}
            )
            self._save()
            return f"![{filename}](/uploads/mock/{filename})"

    def add_note(self, *, project_id: str, issue_iid: str, body: str) -> None:
        with self._lock:
            key = (str(project_id), str(issue_iid))
            if key not in self.issues:
                raise IntegrationError("mock gitlab issue not found", retryable=False, status_code=404)
            self.notes.append({"project_id": str(project_id), "issue_iid": str(issue_iid), "body": body})
            self._save()

    def get_issue(self, *, project_id: str, issue_iid: str) -> GitLabIssue:
        issue = self.issues.get((str(project_id), str(issue_iid)))
        if issue is None:
            raise IntegrationError("mock gitlab issue not found", retryable=False, status_code=404)
        return issue

    def _allocate(
        self,
        *,
        project_id: str,
        title: str,
        description: str,
        labels: list[str],
        assignee_id: str | None,
        marker: str,
    ) -> GitLabIssue:
        self._sequence += 1
        iid = str(self._sequence)
        issue = GitLabIssue(
            id=str(10_000 + self._sequence),
            iid=iid,
            web_url=f"https://gitlab.mock/projects/{project_id}/-/issues/{iid}",
            state="opened",
            project_id=str(project_id),
        )
        self.issues[(str(project_id), iid)] = issue
        self.descriptions[(str(project_id), iid)] = description
        self.created.append(
            {
                "project_id": str(project_id),
                "title": title,
                "description": description,
                "labels": list(labels),
                "assignee_id": assignee_id,
                "marker": marker,
                "iid": iid,
            }
        )
        self._save()
        return issue

    def _drop_marker(self, project_id: str, marker: str) -> None:
        for key, description in list(self.descriptions.items()):
            if key[0] == str(project_id) and marker in description:
                self.issues.pop(key, None)
                self.descriptions.pop(key, None)
        self.created = [
            item for item in self.created if not (item["project_id"] == str(project_id) and item["marker"] == marker)
        ]

    def _load(self) -> None:
        if not self.state_path:
            return
        path = Path(self.state_path)
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        self._sequence = int(data.get("sequence", 0))
        for item in data.get("issues", []):
            issue = GitLabIssue(
                id=str(item["id"]),
                iid=str(item["iid"]),
                web_url=str(item["web_url"]),
                state=str(item["state"]),
                project_id=str(item["project_id"]),
            )
            key = (issue.project_id or "", issue.iid)
            self.issues[key] = issue
            self.descriptions[key] = str(item.get("description") or "")
            self.created.append(
                {
                    "project_id": key[0],
                    "title": item.get("title") or "",
                    "description": self.descriptions[key],
                    "labels": list(item.get("labels") or []),
                    "assignee_id": item.get("assignee_id"),
                    "marker": item.get("marker") or "",
                    "iid": issue.iid,
                }
            )
        self.uploads = list(data.get("uploads") or [])
        self.notes = list(data.get("notes") or [])

    def _save(self) -> None:
        if not self.state_path:
            return
        path = Path(self.state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        issues = []
        for (project_id, iid), issue in self.issues.items():
            created = next((item for item in self.created if item["project_id"] == project_id and item["iid"] == iid), {})
            issues.append(
                {
                    "id": issue.id,
                    "iid": issue.iid,
                    "web_url": issue.web_url,
                    "state": issue.state,
                    "project_id": project_id,
                    "description": self.descriptions.get((project_id, iid), ""),
                    "title": created.get("title", ""),
                    "labels": created.get("labels", []),
                    "assignee_id": created.get("assignee_id"),
                    "marker": created.get("marker", ""),
                }
            )
        payload = {
            "sequence": self._sequence,
            "issues": issues,
            "uploads": self.uploads,
            "notes": self.notes,
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(path)
