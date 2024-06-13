import warnings
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from slixmpp import JID
from sqlalchemy.orm import Mapped, mapped_column

from .meta import Base, JSONSerializable, JSONSerializableTypes


class GatewayUser(Base):
    __tablename__ = "user_account"
    id: Mapped[int] = mapped_column(primary_key=True)
    jid: Mapped[JID] = mapped_column(unique=True)
    registration_date: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now()
    )

    legacy_module_data: Mapped[JSONSerializable] = mapped_column(default={})
    preferences: Mapped[JSONSerializable] = mapped_column(default={})
    avatar_hash: Mapped[Optional[str]] = mapped_column(default=None)

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, jid={self.jid!r})"

    def get(self, field: str, default: str = "") -> JSONSerializableTypes:
        # """
        # Get fields from the registration form (required to comply with slixmpp backend protocol)
        #
        # :param field: Name of the field
        # :param default: Default value to return if the field is not present
        #
        # :return: Value of the field
        # """
        return self.legacy_module_data.get(field, default)

    @property
    def registration_form(self):
        warnings.warn(
            "GatewayUser.registration_form is deprecated.", DeprecationWarning
        )
        return self.legacy_module_data


class Avatar(Base):
    __tablename__ = "avatar"
    id: Mapped[int] = mapped_column(primary_key=True)

    jid: Mapped[JID] = mapped_column(unique=True, index=True)

    filename: Mapped[str] = mapped_column(unique=True)
    hash: Mapped[str] = mapped_column(unique=True)
    height: Mapped[int] = mapped_column()
    width: Mapped[int] = mapped_column()

    legacy_id: Mapped[Optional[str]] = mapped_column(nullable=True)

    url: Mapped[Optional[str]] = mapped_column(default=None)
    etag: Mapped[Optional[str]] = mapped_column(default=None)
    last_modified: Mapped[Optional[str]] = mapped_column(default=None)
