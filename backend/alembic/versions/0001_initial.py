"""Initial schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLES = "('CUSTOMER', 'TESTER', 'DEVELOPER', 'MANAGEMENT', 'ADMIN')"
STATES = (
    "('IDLE', 'AWAITING_PRODUCT', 'AWAITING_IMAGE', 'AWAITING_DESCRIPTION', "
    "'AWAITING_SEVERITY', 'AWAITING_CONFIRMATION', 'SUBMITTING', 'COMPLETED', 'FAILED')"
)
SEVERITIES = "('low', 'medium', 'high', 'critical')"
BUG_STATUSES = "('pending_gitlab', 'opened', 'closed', 'reopened', 'failed')"
OUTBOX_STATUSES = "('pending', 'sending', 'sent', 'dead')"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("phone_e164", sa.String(20), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"role in {ROLES}", name="ck_users_role"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("phone_e164", name="uq_users_phone"),
        sa.UniqueConstraint("token_hash", name="uq_users_token_hash"),
    )
    op.create_index("ix_users_role", "users", ["role"])

    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_customers_code"),
    )

    op.create_table(
        "customer_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("customer_id", "user_id", name="uq_customer_users"),
    )
    op.create_index("ix_customer_users_user", "customer_users", ["user_id"])

    op.create_table(
        "developer_teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_developer_teams_slug"),
    )

    op.create_table(
        "team_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("developer_teams.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_members"),
    )
    op.create_index("ix_team_members_user", "team_members", ["user_id"])

    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("gitlab_project_id", sa.String(255), nullable=False),
        sa.Column("gitlab_project_path", sa.String(255), nullable=False),
        sa.Column(
            "developer_team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("developer_teams.id"),
            nullable=False,
        ),
        sa.Column("default_assignee_gitlab_id", sa.String(64), nullable=True),
        sa.Column("default_labels", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_products_slug"),
    )
    op.create_index("ix_products_team", "products", ["developer_team_id"])

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("wa_id", sa.String(32), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("draft", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"state in {STATES}", name="ck_conversations_state"),
    )
    op.create_index("ix_conversations_wa_id", "conversations", ["wa_id"])
    op.create_index(
        "uq_conversations_one_active",
        "conversations",
        ["wa_id"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('AWAITING_PRODUCT', 'AWAITING_IMAGE', 'AWAITING_DESCRIPTION', "
            "'AWAITING_SEVERITY', 'AWAITING_CONFIRMATION', 'SUBMITTING')"
        ),
    )

    op.create_table(
        "bug_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("reporter_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id"),
            nullable=True,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("gitlab_issue_id", sa.String(64), nullable=True),
        sa.Column("gitlab_issue_iid", sa.String(64), nullable=True),
        sa.Column("gitlab_project_id", sa.String(255), nullable=True),
        sa.Column("gitlab_issue_url", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("correlation_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"severity in {SEVERITIES}", name="ck_bug_reports_severity"),
        sa.CheckConstraint(f"status in {BUG_STATUSES}", name="ck_bug_reports_status"),
        sa.UniqueConstraint("idempotency_key", name="uq_bug_reports_idempotency"),
    )
    op.create_index("ix_bug_reports_customer", "bug_reports", ["customer_id", "created_at"])
    op.create_index("ix_bug_reports_product", "bug_reports", ["product_id"])
    op.create_index("ix_bug_reports_status", "bug_reports", ["status"])
    op.create_index(
        "uq_bug_reports_gitlab_issue",
        "bug_reports",
        ["gitlab_project_id", "gitlab_issue_iid"],
        unique=True,
        postgresql_where=sa.text("gitlab_issue_iid IS NOT NULL"),
    )

    op.create_table(
        "bug_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("bug_report_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bug_reports.id"), nullable=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id"),
            nullable=True,
        ),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("gitlab_markdown", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bug_attachments_bug", "bug_attachments", ["bug_report_id"])

    op.create_table(
        "inbound_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("provider_message_id", sa.String(255), nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id"),
            nullable=True,
        ),
        sa.Column("wa_id", sa.String(32), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider_message_id", name="uq_inbound_messages_provider_id"),
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(100), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "external_id", name="uq_webhook_events_delivery"),
    )

    op.create_table(
        "notification_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("audience", sa.String(32), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "audience in ('customer', 'developer', 'management')",
            name="ck_notification_rules_audience",
        ),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_notification_rules_scope
        ON notification_rules (
            event_type,
            audience,
            COALESCE(product_id, '00000000-0000-0000-0000-000000000000'::uuid)
        )
        """
    )

    op.create_table(
        "notification_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("bug_report_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bug_reports.id"), nullable=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id"),
            nullable=True,
        ),
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("audience", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("delivery_status", sa.String(32), nullable=True),
        sa.Column("dedupe_key", sa.String(700), nullable=False),
        sa.Column("correlation_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"status in {OUTBOX_STATUSES}", name="ck_outbox_status"),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_outbox_dedupe"),
    )
    op.create_index("ix_outbox_pending", "notification_outbox", ["status", "next_attempt_at"])
    op.create_index("ix_outbox_provider_message", "notification_outbox", ["provider_message_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("correlation_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_entity", "audit_logs", ["entity_type", "entity_id"])


def downgrade() -> None:
    for name in (
        "audit_logs",
        "notification_outbox",
        "notification_rules",
        "webhook_events",
        "inbound_messages",
        "bug_attachments",
        "bug_reports",
        "conversations",
        "products",
        "team_members",
        "developer_teams",
        "customer_users",
        "customers",
        "users",
    ):
        op.drop_table(name)
