"""Add Contact.client_type

Revision ID: 3071e0fa69d4
Revises: abba1ae0edb3
Create Date: 2024-07-30 23:12:49.345593

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3071e0fa69d4"
down_revision: Union[str, None] = "abba1ae0edb3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("contact", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "client_type",
                sa.Enum(
                    "bot",
                    "console",
                    "game",
                    "handheld",
                    "pc",
                    "phone",
                    "sms",
                    "tablet",
                    "web",
                    native_enum=False,
                ),
                nullable=False,
                server_default=sa.text("pc"),
            )
        )

    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("contact", schema=None) as batch_op:
        batch_op.drop_column("client_type")

    # ### end Alembic commands ###