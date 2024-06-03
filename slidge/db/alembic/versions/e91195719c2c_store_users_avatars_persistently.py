"""Store users' avatars' hashes persistently

Revision ID: e91195719c2c
Revises: aa9d82a7f6ef
Create Date: 2024-06-01 14:14:51.984943

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e91195719c2c"
down_revision: Union[str, None] = "aa9d82a7f6ef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_account", sa.Column("avatar_hash", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_account", "avatar_hash")
