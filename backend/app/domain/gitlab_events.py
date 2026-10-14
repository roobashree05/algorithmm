from dataclasses import dataclass


@dataclass(frozen=True)
class GitLabIssueEvent:
    project_id: str
    issue_iid: str
    issue_id: str
    action: str
    state: str
    title: str
    url: str


def parse_gitlab_issue_event(payload: dict) -> GitLabIssueEvent | None:
    if payload.get("object_kind") != "issue":
        return None
    attributes = payload.get("object_attributes") or {}
    project = payload.get("project") or {}
    if "iid" not in attributes or "id" not in project:
        return None
    return GitLabIssueEvent(
        project_id=str(project["id"]),
        issue_iid=str(attributes["iid"]),
        issue_id=str(attributes.get("id", "")),
        action=str(attributes.get("action") or "update"),
        state=str(attributes.get("state") or "opened"),
        title=str(attributes.get("title") or ""),
        url=str(attributes.get("url") or ""),
    )


def map_gitlab_status(*, action: str, state: str, current_status: str) -> str:
    if action == "close" or state == "closed":
        return "closed"
    if action == "reopen":
        return "reopened"
    if action == "open":
        return "opened"
    if current_status == "reopened":
        return "reopened"
    return "opened"


def labels_added(payload: dict) -> set[str]:
    changes = payload.get("changes") or {}
    labels = changes.get("labels") or {}
    previous = {item.get("title") for item in labels.get("previous") or [] if isinstance(item, dict)}
    current = {item.get("title") for item in labels.get("current") or [] if isinstance(item, dict)}
    return {label for label in current - previous if label}


def notification_events_for_gitlab(action: str, new_status: str, added_labels: set[str]) -> list[tuple[str, str]]:
    """Return (event_type, audience) pairs for a GitLab issue hook."""
    events: list[tuple[str, str]] = []
    if new_status == "closed":
        events.append(("bug_closed", "customer"))
        events.append(("bug_closed", "management"))
        return events
    if action == "reopen" or new_status == "reopened":
        events.append(("bug_reopened", "customer"))
        events.append(("bug_reopened", "developer"))
        events.append(("bug_reopened", "management"))
        return events
    if action == "open":
        events.append(("issue_assigned", "developer"))
        return events
    events.append(("issue_updated", "developer"))
    if "needs-info" in added_labels:
        events.append(("info_requested", "customer"))
    return events
