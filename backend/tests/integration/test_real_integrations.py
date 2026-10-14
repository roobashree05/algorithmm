"""Opt-in checks against a development GitLab project.

The default suite never collects a passing result from these tests. Set
RUN_REAL_INTEGRATIONS=1 and provide development credentials explicitly.
"""

import os
import uuid

import pytest

from app.integrations.gitlab.rest import GitLabRestProvider

pytestmark = [
    pytest.mark.real_integration,
    pytest.mark.skipif(
        os.getenv("RUN_REAL_INTEGRATIONS") != "1",
        reason="mock suite only; set RUN_REAL_INTEGRATIONS=1 to call a development GitLab project",
    ),
]


def test_real_gitlab_create_is_reused_for_the_same_marker():
    provider = GitLabRestProvider(
        os.environ["GITLAB_BASE_URL"],
        os.environ["GITLAB_ACCESS_TOKEN"],
        timeout=20,
    )
    marker = f"bug-report-id:{uuid.uuid4()}"
    description = f"Local development smoke test.\n\n<!-- {marker} -->"
    first = provider.create_issue(
        project_id=os.environ["GITLAB_PROJECT_ID"],
        title="[low] bugtracker development smoke",
        description=description,
        labels=["bug"],
        assignee_id=None,
        marker=marker,
    )
    second = provider.create_issue(
        project_id=os.environ["GITLAB_PROJECT_ID"],
        title="[low] bugtracker development smoke",
        description=description,
        labels=["bug"],
        assignee_id=None,
        marker=marker,
    )
    assert first.iid
    assert second.iid == first.iid
