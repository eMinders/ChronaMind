"""Session history epoch.

The app identifies a chat message as (session, position, role, text) so that resending the whole
history replays instead of duplicating. Clearing a session's history restarts the positions, so a
message repeated afterwards has the same identity as the one before the clear; the epoch makes the
two distinguishable.

Revision ID: c4a7f1e29b03
Revises: b25d38b0cd7c
Create Date: 2026-11-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a7f1e29b03"  # pragma: allowlist secret
down_revision: Union[str, Sequence[str], None] = "b25d38b0cd7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the history epoch, defaulting existing sessions to their first history."""
    op.add_column("session", sa.Column("history_epoch", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    """Drop the history epoch."""
    op.drop_column("session", "history_epoch")
