"""Rely on DB to store contacts, rooms and participants

Revision ID: 8d2ced764698
Revises: b33993e87db3
Create Date: 2024-07-08 14:39:47.022088

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import slidge.db.meta

# revision identifiers, used by Alembic.
revision: str = "8d2ced764698"
down_revision: Union[str, None] = "b33993e87db3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hat",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("uri", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("title", "uri"),
    )
    op.create_table(
        "contact_sent",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("msg_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contact.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contact_id", "msg_id"),
    )
    op.create_table(
        "participant",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("room_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("is_user", sa.Boolean(), nullable=False),
        sa.Column(
            "affiliation",
            sa.Enum("outcast", "member", "admin", "owner", "none", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.Enum("moderator", "participant", "visitor", "none", native_enum=False),
            nullable=False,
        ),
        sa.Column("presence_sent", sa.Boolean(), nullable=False),
        sa.Column("resource", sa.String(), nullable=True),
        sa.Column("nickname", sa.String(), nullable=True),
        sa.Column("extra_attributes", slidge.db.meta.JSONEncodedDict(), nullable=True),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contact.id"],
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["room.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "participant_hats",
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("hat_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["hat_id"],
            ["hat.id"],
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participant.id"],
        ),
        sa.PrimaryKeyConstraint("participant_id", "hat_id"),
    )
    op.add_column("contact", sa.Column("is_friend", sa.Boolean(), nullable=False))
    op.add_column("contact", sa.Column("added_to_roster", sa.Boolean(), nullable=False))
    op.add_column(
        "contact",
        sa.Column("extra_attributes", slidge.db.meta.JSONEncodedDict(), nullable=True),
    )
    op.add_column("contact", sa.Column("updated", sa.Boolean(), nullable=False))
    op.add_column("room", sa.Column("description", sa.String(), nullable=True))
    op.add_column("room", sa.Column("subject", sa.String(), nullable=True))
    op.add_column("room", sa.Column("subject_date", sa.DateTime(), nullable=True))

    if op.get_bind().engine.name == "postgresql":
        op.execute(
            "CREATE TYPE muctype AS ENUM ('GROUP', 'CHANNEL', 'CHANNEL_NON_ANONYMOUS')"
        )

    op.add_column(
        "room",
        sa.Column(
            "muc_type",
            sa.Enum("GROUP", "CHANNEL", "CHANNEL_NON_ANONYMOUS", name="muctype"),
            nullable=True,
        ),
    )
    op.add_column("room", sa.Column("user_resources", sa.String(), nullable=True))
    op.add_column(
        "room", sa.Column("participants_filled", sa.Boolean(), nullable=False)
    )
    op.add_column(
        "room",
        sa.Column("extra_attributes", slidge.db.meta.JSONEncodedDict(), nullable=True),
    )
    op.add_column("room", sa.Column("updated", sa.Boolean(), nullable=False))


def downgrade() -> None:
    op.drop_column("room", "updated")
    op.drop_column("room", "extra_attributes")
    op.drop_column("room", "participants_filled")
    op.drop_column("room", "user_resources")
    op.drop_column("room", "muc_type")
    op.drop_column("room", "subject_date")
    op.drop_column("room", "subject")
    op.drop_column("room", "description")
    op.drop_column("contact", "updated")
    op.drop_column("contact", "extra_attributes")
    op.drop_column("contact", "added_to_roster")
    op.drop_column("contact", "is_friend")
    op.drop_table("participant_hats")
    op.drop_table("participant")
    op.drop_table("contact_sent")
    op.drop_table("hat")
