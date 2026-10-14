import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture(scope="session")
def database_url():
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        pytest.skip("DATABASE_URL is not set; integration tests run inside Docker Compose")
    admin = raw.replace("postgresql+psycopg://", "postgresql://")
    admin_dsn = admin.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("bugtracker_test",))
            if cursor.fetchone() is None:
                cursor.execute('CREATE DATABASE "bugtracker_test"')
    test_url = raw.rsplit("/", 1)[0] + "/bugtracker_test"
    os.environ["DATABASE_URL"] = test_url
    os.environ["WHATSAPP_PROVIDER"] = "mock"
    os.environ["GITLAB_PROVIDER"] = "mock"
    get_settings.cache_clear()
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", test_url)
    command.upgrade(config, "head")
    return test_url


@pytest.fixture
def settings(database_url, tmp_path):
    return Settings(
        app_env="test",
        database_url=database_url,
        redis_url="",
        whatsapp_provider="mock",
        whatsapp_app_secret="test-whatsapp-secret",
        whatsapp_verify_token="test-verify-token",
        gitlab_provider="mock",
        gitlab_webhook_secret="test-gitlab-secret",
        gitlab_mock_state_path="",
        attachment_dir=str(tmp_path / "attachments"),
        attachment_max_bytes=1024,
        enable_outbox_worker=False,
        seed_demo_data=False,
        rate_limit_per_minute=10000,
        notification_backoff_base_seconds=2,
        notification_backoff_cap_seconds=30,
        notification_max_attempts=3,
        conversation_ttl_minutes=60,
        log_level="WARNING",
    )


@pytest.fixture
def client(settings):
    application = create_app(settings)
    with application.state.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            TRUNCATE TABLE
              audit_logs,
              notification_outbox,
              notification_rules,
              webhook_events,
              inbound_messages,
              bug_attachments,
              bug_reports,
              conversations,
              team_members,
              products,
              developer_teams,
              customer_users,
              users,
              customers
            RESTART IDENTITY CASCADE
            """
        )
    with TestClient(application) as test_client:
        yield test_client
