from dataclasses import dataclass


@dataclass(frozen=True)
class GitLabIssue:
    id: str
    iid: str
    web_url: str
    state: str
    project_id: str | None = None


class GitLabProvider:
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
        raise NotImplementedError

    def upload_file(self, *, project_id: str, filename: str, content: bytes, content_type: str) -> str:
        raise NotImplementedError

    def add_note(self, *, project_id: str, issue_iid: str, body: str) -> None:
        raise NotImplementedError

    def get_issue(self, *, project_id: str, issue_iid: str) -> GitLabIssue:
        raise NotImplementedError
