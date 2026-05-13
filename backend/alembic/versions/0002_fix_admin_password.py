"""Fix admin password hash — sets admin/admin123 correctly.

Revision: 0002
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The hash in 0001 was invalid. This corrects it for existing deployments.
    # Hash corresponds to password: admin123
    op.execute("""
    UPDATE users
    SET password_hash    = '$2b$12$Xwb6rR5WO/2BjdgDfhYQh.V5G74u/l.H3cIPgQaCYyjTFpzZny7n.',
        initial_password = 'admin123'
    WHERE username = 'admin'
    """)


def downgrade() -> None:
    pass
