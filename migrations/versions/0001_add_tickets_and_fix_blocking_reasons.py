"""add_tickets_and_fix_blocking_reasons

Revision ID: 0001
Revises:
Create Date: 2026-05-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create moderators table (new schema with role instead of is_admin)
    op.create_table(
        'moderators',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('first_name', sa.String(100), nullable=False),
        sa.Column('last_name', sa.String(100), nullable=True),
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('role', sa.String(20), nullable=False, server_default='MODERATOR'),
        sa.Column('category_specializations', postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )
    op.create_index('ix_moderators_email', 'moderators', ['email'])

    # Create refresh_tokens table
    op.create_table(
        'refresh_tokens',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('token', sa.String(), nullable=False),
        sa.Column('account_id', sa.String(), nullable=False),
        sa.Column('account_type', sa.String(50), nullable=False),
        sa.Column('revoked', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )

    # Create product_blocking_reasons table (new schema with code, description, is_active)
    op.create_table(
        'product_blocking_reasons',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('code', sa.String(64), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('hard_block', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )

    # Create product_moderation table (legacy, kept for backwards compat)
    op.create_table(
        'product_moderation',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('product_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('seller_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(25), nullable=False),
        sa.Column('queue_priority', sa.Integer(), nullable=False),
        sa.Column('total_active_quantity', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('json_before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('json_after', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('blocking_reason_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('moderator_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('moderator_comment', sa.Text(), nullable=True),
        sa.Column('date_created', sa.DateTime(timezone=True), nullable=False),
        sa.Column('date_updated', sa.DateTime(timezone=True), nullable=False),
        sa.Column('date_moderation', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('queue_priority >= 1 AND queue_priority <= 4', name='chk_queue_priority'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id'),
    )

    # Create product_moderation_field_report table (legacy)
    op.create_table(
        'product_moderation_field_report',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('product_moderation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('field_name', sa.String(100), nullable=False),
        sa.Column('sku_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('comment', sa.Text(), nullable=False),
        sa.Column('date_created', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['product_moderation_id'], ['product_moderation.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_product_moderation_field_report_product_moderation_id',
        'product_moderation_field_report',
        ['product_moderation_id'],
    )

    # Create tickets table
    op.create_table(
        'tickets',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('product_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('seller_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('category_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='PENDING'),
        sa.Column('queue_priority', sa.Integer(), nullable=False, server_default=sa.text('3')),
        sa.Column('assigned_moderator_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('claim_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decision_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('json_before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('json_after', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('decision_comment', sa.Text(), nullable=True),
        sa.Column('idempotency_key', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('queue_priority >= 1 AND queue_priority <= 4', name='chk_ticket_queue_priority'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key'),
    )
    op.create_index('ix_tickets_product_id', 'tickets', ['product_id'])
    op.create_index('ix_tickets_seller_id', 'tickets', ['seller_id'])

    # Create ticket_history table
    op.create_table(
        'ticket_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('ticket_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('action', sa.String(30), nullable=False),
        sa.Column('moderator_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ticket_history_ticket_id', 'ticket_history', ['ticket_id'])


def downgrade() -> None:
    op.drop_table('ticket_history')
    op.drop_table('tickets')
    op.drop_table('product_moderation_field_report')
    op.drop_table('product_moderation')
    op.drop_table('product_blocking_reasons')
    op.drop_table('refresh_tokens')
    op.drop_table('moderators')
