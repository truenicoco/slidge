import warnings
from datetime import datetime
from enum import IntEnum
from typing import Optional

import sqlalchemy as sa
from slixmpp import JID
from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .meta import Base, JSONSerializable, JSONSerializableTypes


class XmppToLegacyEnum(IntEnum):
    """
    XMPP-client generated IDs, used in the XmppToLegacyIds table to keep track
    of corresponding legacy IDs
    """

    DM = 1
    GROUP_CHAT = 2
    THREAD = 3


class GatewayUser(Base):
    """
    A user, registered to the gateway component.
    """

    __tablename__ = "user_account"
    id: Mapped[int] = mapped_column(primary_key=True)
    jid: Mapped[JID] = mapped_column(unique=True)
    registration_date: Mapped[datetime] = mapped_column(
        sa.DateTime, server_default=sa.func.now()
    )

    legacy_module_data: Mapped[JSONSerializable] = mapped_column(default={})
    """
    Arbitrary non-relational data that legacy modules can use
    """
    preferences: Mapped[JSONSerializable] = mapped_column(default={})
    avatar_hash: Mapped[Optional[str]] = mapped_column(default=None)
    """
    Hash of the user's avatar, to avoid re-publishing the same avatar on the
    legacy network
    """

    contacts: Mapped[list["Contact"]] = relationship(cascade="all, delete-orphan")
    rooms: Mapped[list["Room"]] = relationship(cascade="all, delete-orphan")
    xmpp_to_legacy: Mapped[list["XmppToLegacyIds"]] = relationship(
        cascade="all, delete-orphan"
    )
    attachments: Mapped[list["Attachment"]] = relationship(cascade="all, delete-orphan")
    multi_legacy: Mapped[list["LegacyIdsMulti"]] = relationship(
        cascade="all, delete-orphan"
    )
    multi_xmpp: Mapped[list["XmppIdsMulti"]] = relationship(
        cascade="all, delete-orphan"
    )

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
    def registration_form(self) -> dict:
        # Kept for retrocompat, should be
        # FIXME: delete me
        warnings.warn(
            "GatewayUser.registration_form is deprecated.", DeprecationWarning
        )
        return self.legacy_module_data


class Avatar(Base):
    """
    Avatars of contacts, rooms and participants.

    To comply with XEPs, we convert them all to PNG before storing them.
    """

    __tablename__ = "avatar"

    id: Mapped[int] = mapped_column(primary_key=True)

    filename: Mapped[str] = mapped_column(unique=True)
    hash: Mapped[str] = mapped_column(unique=True)
    height: Mapped[int] = mapped_column()
    width: Mapped[int] = mapped_column()

    # legacy network-wide unique identifier for the avatar
    legacy_id: Mapped[Optional[str]] = mapped_column(unique=True, nullable=True)

    # this is only used when avatars are available as HTTP URLs and do not
    # have a legacy_id
    url: Mapped[Optional[str]] = mapped_column(default=None)
    etag: Mapped[Optional[str]] = mapped_column(default=None)
    last_modified: Mapped[Optional[str]] = mapped_column(default=None)

    contacts: Mapped[list["Contact"]] = relationship(back_populates="avatar")
    rooms: Mapped[list["Room"]] = relationship(back_populates="avatar")


class Contact(Base):
    """
    Legacy contacts
    """

    __tablename__ = "contact"
    __table_args__ = (
        UniqueConstraint("user_account_id", "legacy_id"),
        UniqueConstraint("user_account_id", "jid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_account_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[GatewayUser] = relationship(back_populates="contacts")
    legacy_id: Mapped[str] = mapped_column(nullable=False)

    jid: Mapped[JID] = mapped_column()

    avatar_id: Mapped[int] = mapped_column(ForeignKey("avatar.id"), nullable=True)
    avatar: Mapped[Avatar] = relationship(back_populates="contacts")

    nick: Mapped[Optional[str]] = mapped_column(nullable=True)

    cached_presence: Mapped[bool] = mapped_column(default=False)
    last_seen: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    ptype: Mapped[Optional[str]] = mapped_column(nullable=True)
    pstatus: Mapped[Optional[str]] = mapped_column(nullable=True)
    pshow: Mapped[Optional[str]] = mapped_column(nullable=True)


class Room(Base):
    """
    Legacy room
    """

    __tablename__ = "room"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_account_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[GatewayUser] = relationship(back_populates="rooms")
    legacy_id: Mapped[str] = mapped_column(unique=True, nullable=False)

    jid: Mapped[JID] = mapped_column(unique=True)

    avatar_id: Mapped[int] = mapped_column(ForeignKey("avatar.id"), nullable=True)
    avatar: Mapped[Avatar] = relationship(back_populates="rooms")

    name: Mapped[Optional[str]] = mapped_column(nullable=True)


class ArchivedMessage(Base):
    """
    Messages of rooms, that we store to act as a MAM server
    """

    __tablename__ = "mam"
    __table_args__ = (UniqueConstraint("room_id", "stanza_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("room.id"), nullable=False)

    stanza_id: Mapped[str] = mapped_column(nullable=False)
    timestamp: Mapped[datetime] = mapped_column(nullable=False)
    author_jid: Mapped[JID] = mapped_column(nullable=False)

    stanza: Mapped[str] = mapped_column(nullable=False)


class XmppToLegacyIds(Base):
    """
    XMPP-client generated IDs, and mapping to the corresponding legacy IDs
    """

    __tablename__ = "xmpp_to_legacy_ids"
    __table_args__ = (
        Index("xmpp_legacy", "user_account_id", "xmpp_id", "legacy_id", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_account_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[GatewayUser] = relationship(back_populates="xmpp_to_legacy")

    xmpp_id: Mapped[str] = mapped_column(nullable=False)
    legacy_id: Mapped[str] = mapped_column(nullable=False)

    type: Mapped[XmppToLegacyEnum] = mapped_column(nullable=False)


class Attachment(Base):
    """
    Legacy attachments
    """

    __tablename__ = "attachment"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_account_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[GatewayUser] = relationship(back_populates="attachments")

    legacy_file_id: Mapped[Optional[str]] = mapped_column(index=True, nullable=True)
    url: Mapped[str] = mapped_column(index=True, nullable=False)
    sims: Mapped[Optional[str]] = mapped_column()
    sfs: Mapped[Optional[str]] = mapped_column()


class LegacyIdsMulti(Base):
    """
    Legacy messages with multiple attachments are split as several XMPP messages,
    this table and the next maps a single legacy ID to multiple XMPP IDs.
    """

    __tablename__ = "legacy_ids_multi"
    __table_args__ = (
        Index(
            "legacy_ids_multi_user_account_id_legacy_id",
            "user_account_id",
            "legacy_id",
            unique=True,
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_account_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))

    legacy_id: Mapped[str] = mapped_column(nullable=False)
    xmpp_ids: Mapped[list["XmppIdsMulti"]] = relationship(
        back_populates="legacy_ids_multi"
    )


class XmppIdsMulti(Base):
    __tablename__ = "xmpp_ids_multi"
    __table_args__ = (
        Index(
            "legacy_ids_multi_user_account_id_xmpp_id",
            "user_account_id",
            "xmpp_id",
            unique=True,
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_account_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))

    xmpp_id: Mapped[str] = mapped_column(nullable=False)

    legacy_ids_multi_id: Mapped[int] = mapped_column(ForeignKey("legacy_ids_multi.id"))
    legacy_ids_multi: Mapped[LegacyIdsMulti] = relationship(back_populates="xmpp_ids")
