import warnings
from datetime import datetime
from enum import IntEnum
from typing import Optional

import sqlalchemy as sa
from slixmpp import JID
from slixmpp.types import MucAffiliation, MucRole
from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..util.types import ClientType, MucType
from .meta import Base, JSONSerializable, JSONSerializableTypes


class XmppToLegacyEnum(IntEnum):
    """
    XMPP-client generated IDs, used in the XmppToLegacyIds table to keep track
    of corresponding legacy IDs
    """

    DM = 1
    GROUP_CHAT = 2
    THREAD = 3


class ArchivedMessageSource(IntEnum):
    """
    Whether an archived message comes from ``LegacyMUC.backfill()`` or was received
    as a "live" message.
    """

    LIVE = 1
    BACKFILL = 2


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

    contacts: Mapped[list["Contact"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    rooms: Mapped[list["Room"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
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
    caps_ver: Mapped[Optional[str]] = mapped_column(nullable=True)

    is_friend: Mapped[bool] = mapped_column(default=False)
    added_to_roster: Mapped[bool] = mapped_column(default=False)
    sent_order: Mapped[list["ContactSent"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )

    extra_attributes: Mapped[Optional[JSONSerializable]] = mapped_column(
        default=None, nullable=True
    )
    updated: Mapped[bool] = mapped_column(default=False)

    vcard: Mapped[Optional[str]] = mapped_column()
    vcard_fetched: Mapped[bool] = mapped_column(default=False)

    participants: Mapped[list["Participant"]] = relationship(back_populates="contact")

    avatar_legacy_id: Mapped[Optional[str]] = mapped_column(nullable=True)

    client_type: Mapped[ClientType] = mapped_column(nullable=False, default="pc")


class ContactSent(Base):
    """
    Keep track of XMPP msg ids sent by a specific contact for networks in which
    all messages need to be marked as read.

    (XMPP displayed markers convey a "read up to here" semantic.)
    """

    __tablename__ = "contact_sent"
    __table_args__ = (UniqueConstraint("contact_id", "msg_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id"))
    contact: Mapped[Contact] = relationship(back_populates="sent_order")
    msg_id: Mapped[str] = mapped_column()


class Room(Base):
    """
    Legacy room
    """

    __table_args__ = (
        UniqueConstraint(
            "user_account_id", "legacy_id", name="uq_room_user_account_id_legacy_id"
        ),
        UniqueConstraint("user_account_id", "jid", name="uq_room_user_account_id_jid"),
    )

    __tablename__ = "room"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_account_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"))
    user: Mapped[GatewayUser] = relationship(back_populates="rooms")
    legacy_id: Mapped[str] = mapped_column(nullable=False)

    jid: Mapped[JID] = mapped_column(nullable=False)

    avatar_id: Mapped[int] = mapped_column(ForeignKey("avatar.id"), nullable=True)
    avatar: Mapped[Avatar] = relationship(back_populates="rooms")

    name: Mapped[Optional[str]] = mapped_column(nullable=True)
    description: Mapped[Optional[str]] = mapped_column(nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(nullable=True)
    subject_date: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    subject_setter: Mapped[Optional[str]] = mapped_column(nullable=True)

    n_participants: Mapped[Optional[int]] = mapped_column(default=None)

    muc_type: Mapped[Optional[MucType]] = mapped_column(default=MucType.GROUP)

    user_nick: Mapped[Optional[str]] = mapped_column()
    user_resources: Mapped[Optional[str]] = mapped_column(nullable=True)

    participants_filled: Mapped[bool] = mapped_column(default=False)
    history_filled: Mapped[bool] = mapped_column(default=False)

    extra_attributes: Mapped[Optional[JSONSerializable]] = mapped_column(default=None)
    updated: Mapped[bool] = mapped_column(default=False)

    participants: Mapped[list["Participant"]] = relationship(
        back_populates="room",
        primaryjoin="Participant.room_id == Room.id",
        cascade="all, delete-orphan",
    )

    avatar_legacy_id: Mapped[Optional[str]] = mapped_column(nullable=True)

    archive: Mapped[list["ArchivedMessage"]] = relationship(
        cascade="all, delete-orphan"
    )


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
    source: Mapped[ArchivedMessageSource] = mapped_column(nullable=False)
    legacy_id: Mapped[Optional[str]] = mapped_column(nullable=True)

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
        back_populates="legacy_ids_multi", cascade="all, delete-orphan"
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


participant_hats = sa.Table(
    "participant_hats",
    Base.metadata,
    sa.Column("participant_id", ForeignKey("participant.id"), primary_key=True),
    sa.Column("hat_id", ForeignKey("hat.id"), primary_key=True),
)


class Hat(Base):
    __tablename__ = "hat"
    __table_args__ = (UniqueConstraint("title", "uri"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column()
    uri: Mapped[str] = mapped_column()
    participants: Mapped[list["Participant"]] = relationship(
        secondary=participant_hats, back_populates="hats"
    )


class Participant(Base):
    __tablename__ = "participant"

    id: Mapped[int] = mapped_column(primary_key=True)

    room_id: Mapped[int] = mapped_column(ForeignKey("room.id"), nullable=False)
    room: Mapped[Room] = relationship(
        back_populates="participants", primaryjoin=Room.id == room_id
    )

    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id"), nullable=True)
    contact: Mapped[Contact] = relationship(lazy=False, back_populates="participants")

    is_user: Mapped[bool] = mapped_column(default=False)

    affiliation: Mapped[MucAffiliation] = mapped_column(default="member")
    role: Mapped[MucRole] = mapped_column(default="participant")

    presence_sent: Mapped[bool] = mapped_column(default=False)

    resource: Mapped[Optional[str]] = mapped_column(default=None)
    nickname: Mapped[str] = mapped_column(nullable=True, default=None)

    hats: Mapped[list["Hat"]] = relationship(
        secondary=participant_hats, back_populates="participants"
    )

    extra_attributes: Mapped[Optional[JSONSerializable]] = mapped_column(default=None)


class Bob(Base):
    __tablename__ = "bob"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_name: Mapped[str] = mapped_column(nullable=False)

    sha_1: Mapped[str] = mapped_column(nullable=False, unique=True)
    sha_256: Mapped[str] = mapped_column(nullable=False, unique=True)
    sha_512: Mapped[str] = mapped_column(nullable=False, unique=True)

    content_type: Mapped[Optional[str]] = mapped_column(nullable=False)
