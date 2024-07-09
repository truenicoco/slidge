"""Store subject setter in Room

Revision ID: 29f5280c61aa
Revises: 8d2ced764698
Create Date: 2024-07-10 13:09:25.181594

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "29f5280c61aa"
down_revision: Union[str, None] = "8d2ced764698"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("room", schema=None) as batch_op:
        batch_op.add_column(sa.Column("subject_setter_id", sa.Integer(), nullable=True))
        # we give this constraint a name a workaround for
        # https://github.com/sqlalchemy/alembic/issues/1195
        batch_op.create_foreign_key(
            "subject_setter_id_foreign_key",
            "participant",
            ["subject_setter_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("room", schema=None) as batch_op:
        batch_op.drop_constraint("subject_setter_id_foreign_key", type_="foreignkey")
        batch_op.drop_column("subject_setter_id")
