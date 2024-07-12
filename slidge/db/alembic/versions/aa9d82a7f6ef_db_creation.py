"""DB Creation

Including a migration from the user_store shelf

Revision ID: aa9d82a7f6ef
Revises:
Create Date: 2024-04-17 20:57:01.357041

"""

import logging
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import slidge.db.meta

# revision identifiers, used by Alembic.
revision: str = "aa9d82a7f6ef"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    accounts = op.create_table(
        "user_account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("jid", slidge.db.meta.JIDType(), nullable=False),
        sa.Column(
            "registration_date",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "legacy_module_data", slidge.db.meta.JSONEncodedDict(), nullable=False
        ),
        sa.Column("preferences", slidge.db.meta.JSONEncodedDict(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jid"),
    )
    # ### end Alembic commands ###
    try:
        migrate_from_shelf(accounts)
    except Exception:
        downgrade()
        raise


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("user_account")
    # ### end Alembic commands ###


def migrate_from_shelf(accounts: sa.Table) -> None:
    try:
        from slidge.util.db import user_store
    except ImportError:
        return
    try:
        users = list(user_store.get_all())
    except AttributeError:
        return
    logging.info("Migrating %s users from the deprecated user_store shelf", len(users))
    op.bulk_insert(
        accounts,
        [
            {
                "jid": user.jid,
                "registration_date": (
                    user.registration_date
                    if user.registration_date is not None
                    else datetime.now()
                ),
                "legacy_module_data": user.registration_form,
                "preferences": {},
            }
            for user in users
        ],
    )
