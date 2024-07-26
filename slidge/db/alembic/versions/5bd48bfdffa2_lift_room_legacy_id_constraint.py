"""Lift room legacy ID constraint

Revision ID: 5bd48bfdffa2
Revises: b64b1a793483
Create Date: 2024-07-24 10:29:23.467851

"""

from typing import Sequence, Union

from alembic import op

from slidge.db.models import Room

# revision identifiers, used by Alembic.
revision: str = "5bd48bfdffa2"
down_revision: Union[str, None] = "b64b1a793483"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table(
        "room",
        schema=None,
        # without copy_from, the newly created table keeps the constraints
        # we actually want to ditch.
        copy_from=Room.__table__,  # type:ignore
    ) as batch_op:
        batch_op.create_unique_constraint(
            "uq_room_user_account_id_jid", ["user_account_id", "jid"]
        )
        batch_op.create_unique_constraint(
            "uq_room_user_account_id_legacy_id", ["user_account_id", "legacy_id"]
        )


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("room", schema=None) as batch_op:
        batch_op.drop_constraint("uq_room_user_account_id_legacy_id", type_="unique")
        batch_op.drop_constraint("uq_room_user_account_id_jid", type_="unique")

    # ### end Alembic commands ###