from app.core.config import Settings
from app.integrations.gitlab.mock import MockGitLabProvider
from app.integrations.gitlab.rest import GitLabRestProvider


def build_gitlab_provider(settings: Settings):
    if settings.gitlab_provider == "rest":
        return GitLabRestProvider(
            base_url=settings.gitlab_base_url,
            token=settings.gitlab_access_token,
            timeout=settings.gitlab_timeout_seconds,
        )
    return MockGitLabProvider(state_path=settings.gitlab_mock_state_path or None)
