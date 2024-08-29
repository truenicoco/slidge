from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from mimetypes import guess_extension
from typing import TYPE_CHECKING, Collection, Iterator, Optional, Type

from slixmpp import JID, Iq, Message, Presence
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0231.stanza import BitsOfBinary
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session, attributes, load_only
from sqlalchemy.sql.functions import count

from ..core import config
from ..util.archive_msg import HistoryMessage
from ..util.types import URL, CachedPresence, ClientType
from ..util.types import Hat as HatTuple
from ..util.types import MamMetadata, MucAffiliation, MucRole, Sticker
from .meta import Base
from .models import (
    ArchivedMessage,
    ArchivedMessageSource,
    Attachment,
    Avatar,
    Bob,
    Contact,
    ContactSent,
    GatewayUser,
    Hat,
    LegacyIdsMulti,
    Participant,
    Room,
    XmppIdsMulti,
    XmppToLegacyEnum,
    XmppToLegacyIds,
    participant_hats,
)

if TYPE_CHECKING:
    from ..contact.contact import LegacyContact
    from ..group.participant import LegacyParticipant
    from ..group.room import LegacyMUC


class EngineMixin:
    def __init__(self, engine: Engine):
        self._engine = engine

    @contextmanager
    def session(self, **session_kwargs) -> Iterator[Session]:
        global _session
        if _session is not None:
            yield _session
            return
        with Session(self._engine, **session_kwargs) as session:
            _session = session
            try:
                yield session
            finally:
                _session = None


class UpdatedMixin(EngineMixin):
    model: Type[Base] = NotImplemented

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        with self.session() as session:
            session.execute(update(self.model).values(updated=False))  # type:ignore
            session.commit()

    def get_by_pk(self, pk: int) -> Optional[Base]:
        with self.session() as session:
            return session.execute(
                select(self.model).where(self.model.id == pk)  # type:ignore
            ).scalar()


class SlidgeStore(EngineMixin):
    def __init__(self, engine: Engine):
        super().__init__(engine)
        self.users = UserStore(engine)
        self.avatars = AvatarStore(engine)
        self.contacts = ContactStore(engine)
        self.mam = MAMStore(engine)
        self.multi = MultiStore(engine)
        self.attachments = AttachmentStore(engine)
        self.rooms = RoomStore(engine)
        self.sent = SentStore(engine)
        self.participants = ParticipantStore(engine)
        self.bob = BobStore(engine)


class UserStore(EngineMixin):
    def new(self, jid: JID, legacy_module_data: dict) -> GatewayUser:
        if jid.resource:
            jid = JID(jid.bare)
        with self.session(expire_on_commit=False) as session:
            user = session.execute(
                select(GatewayUser).where(GatewayUser.jid == jid)
            ).scalar()
            if user is not None:
                return user
            user = GatewayUser(jid=jid, legacy_module_data=legacy_module_data)
            session.add(user)
            session.commit()
        return user

    def update(self, user: GatewayUser):
        # https://github.com/sqlalchemy/sqlalchemy/discussions/6473
        attributes.flag_modified(user, "legacy_module_data")
        attributes.flag_modified(user, "preferences")
        with self.session() as session:
            session.add(user)
            session.commit()

    def get_all(self) -> Iterator[GatewayUser]:
        with self.session() as session:
            yield from session.execute(select(GatewayUser)).scalars()

    def get(self, jid: JID) -> Optional[GatewayUser]:
        with self.session() as session:
            return session.execute(
                select(GatewayUser).where(GatewayUser.jid == jid.bare)
            ).scalar()

    def get_by_stanza(self, stanza: Iq | Message | Presence) -> Optional[GatewayUser]:
        return self.get(stanza.get_from())

    def delete(self, jid: JID) -> None:
        with self.session() as session:
            session.delete(self.get(jid))
            session.commit()

    def set_avatar_hash(self, pk: int, h: str | None = None) -> None:
        with self.session() as session:
            session.execute(
                update(GatewayUser).where(GatewayUser.id == pk).values(avatar_hash=h)
            )
            session.commit()


class AvatarStore(EngineMixin):
    def get_by_url(self, url: URL | str) -> Optional[Avatar]:
        with self.session() as session:
            return session.execute(select(Avatar).where(Avatar.url == url)).scalar()

    def get_by_pk(self, pk: int) -> Optional[Avatar]:
        with self.session() as session:
            return session.execute(select(Avatar).where(Avatar.id == pk)).scalar()

    def delete_by_pk(self, pk: int):
        with self.session() as session:
            session.execute(delete(Avatar).where(Avatar.id == pk))
            session.commit()

    def get_all(self) -> Iterator[Avatar]:
        with self.session() as session:
            yield from session.execute(select(Avatar)).scalars()


class SentStore(EngineMixin):
    def set_message(self, user_pk: int, legacy_id: str, xmpp_id: str) -> None:
        with self.session() as session:
            msg = (
                session.query(XmppToLegacyIds)
                .filter(XmppToLegacyIds.user_account_id == user_pk)
                .filter(XmppToLegacyIds.legacy_id == legacy_id)
                .filter(XmppToLegacyIds.xmpp_id == xmpp_id)
                .scalar()
            )
            if msg is None:
                msg = XmppToLegacyIds(user_account_id=user_pk)
            else:
                log.debug("Resetting a DM from sent store")
            msg.legacy_id = legacy_id
            msg.xmpp_id = xmpp_id
            msg.type = XmppToLegacyEnum.DM
            session.add(msg)
            session.commit()

    def get_xmpp_id(self, user_pk: int, legacy_id: str) -> Optional[str]:
        with self.session() as session:
            return session.execute(
                select(XmppToLegacyIds.xmpp_id)
                .where(XmppToLegacyIds.user_account_id == user_pk)
                .where(XmppToLegacyIds.legacy_id == legacy_id)
                .where(XmppToLegacyIds.type == XmppToLegacyEnum.DM)
            ).scalar()

    def get_legacy_id(self, user_pk: int, xmpp_id: str) -> Optional[str]:
        with self.session() as session:
            return session.execute(
                select(XmppToLegacyIds.legacy_id)
                .where(XmppToLegacyIds.user_account_id == user_pk)
                .where(XmppToLegacyIds.xmpp_id == xmpp_id)
                .where(XmppToLegacyIds.type == XmppToLegacyEnum.DM)
            ).scalar()

    def set_group_message(self, user_pk: int, legacy_id: str, xmpp_id: str) -> None:
        with self.session() as session:
            msg = XmppToLegacyIds(
                user_account_id=user_pk,
                legacy_id=legacy_id,
                xmpp_id=xmpp_id,
                type=XmppToLegacyEnum.GROUP_CHAT,
            )
            session.add(msg)
            session.commit()

    def get_group_xmpp_id(self, user_pk: int, legacy_id: str) -> Optional[str]:
        with self.session() as session:
            return session.execute(
                select(XmppToLegacyIds.xmpp_id)
                .where(XmppToLegacyIds.user_account_id == user_pk)
                .where(XmppToLegacyIds.legacy_id == legacy_id)
                .where(XmppToLegacyIds.type == XmppToLegacyEnum.GROUP_CHAT)
            ).scalar()

    def get_group_legacy_id(self, user_pk: int, xmpp_id: str) -> Optional[str]:
        with self.session() as session:
            return session.execute(
                select(XmppToLegacyIds.legacy_id)
                .where(XmppToLegacyIds.user_account_id == user_pk)
                .where(XmppToLegacyIds.xmpp_id == xmpp_id)
                .where(XmppToLegacyIds.type == XmppToLegacyEnum.GROUP_CHAT)
            ).scalar()

    def set_thread(self, user_pk: int, legacy_id: str, xmpp_id: str) -> None:
        with self.session() as session:
            msg = XmppToLegacyIds(
                user_account_id=user_pk,
                legacy_id=legacy_id,
                xmpp_id=xmpp_id,
                type=XmppToLegacyEnum.THREAD,
            )
            session.add(msg)
            session.commit()

    def get_legacy_thread(self, user_pk: int, xmpp_id: str) -> Optional[str]:
        with self.session() as session:
            return session.execute(
                select(XmppToLegacyIds.legacy_id)
                .where(XmppToLegacyIds.user_account_id == user_pk)
                .where(XmppToLegacyIds.xmpp_id == xmpp_id)
                .where(XmppToLegacyIds.type == XmppToLegacyEnum.THREAD)
            ).scalar()

    def was_sent_by_user(self, user_pk: int, legacy_id: str) -> bool:
        with self.session() as session:
            return (
                session.execute(
                    select(XmppToLegacyIds.legacy_id)
                    .where(XmppToLegacyIds.user_account_id == user_pk)
                    .where(XmppToLegacyIds.legacy_id == legacy_id)
                ).scalar()
                is not None
            )


class ContactStore(UpdatedMixin):
    model = Contact

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        with self.session() as session:
            session.execute(update(Contact).values(cached_presence=False))
            session.commit()

    def get_all(self, user_pk: int) -> Iterator[Contact]:
        with self.session() as session:
            yield from session.execute(
                select(Contact).where(Contact.user_account_id == user_pk)
            ).scalars()

    def get_by_jid(self, user_pk: int, jid: JID) -> Optional[Contact]:
        with self.session() as session:
            return session.execute(
                select(Contact)
                .where(Contact.jid == jid.bare)
                .where(Contact.user_account_id == user_pk)
            ).scalar()

    def get_by_legacy_id(self, user_pk: int, legacy_id: str) -> Optional[Contact]:
        with self.session() as session:
            return session.execute(
                select(Contact)
                .where(Contact.legacy_id == legacy_id)
                .where(Contact.user_account_id == user_pk)
            ).scalar()

    def update_nick(self, contact_pk: int, nick: Optional[str]) -> None:
        with self.session() as session:
            session.execute(
                update(Contact).where(Contact.id == contact_pk).values(nick=nick)
            )
            session.commit()

    def get_presence(self, contact_pk: int) -> Optional[CachedPresence]:
        with self.session() as session:
            presence = session.execute(
                select(
                    Contact.last_seen,
                    Contact.ptype,
                    Contact.pstatus,
                    Contact.pshow,
                    Contact.cached_presence,
                ).where(Contact.id == contact_pk)
            ).first()
            if presence is None or not presence[-1]:
                return None
            return CachedPresence(*presence[:-1])

    def set_presence(self, contact_pk: int, presence: CachedPresence) -> None:
        with self.session() as session:
            session.execute(
                update(Contact)
                .where(Contact.id == contact_pk)
                .values(**presence._asdict(), cached_presence=True)
            )
            session.commit()

    def reset_presence(self, contact_pk: int):
        with self.session() as session:
            session.execute(
                update(Contact)
                .where(Contact.id == contact_pk)
                .values(
                    last_seen=None,
                    ptype=None,
                    pstatus=None,
                    pshow=None,
                    cached_presence=False,
                )
            )
            session.commit()

    def set_avatar(
        self, contact_pk: int, avatar_pk: Optional[int], avatar_legacy_id: Optional[str]
    ):
        with self.session() as session:
            session.execute(
                update(Contact)
                .where(Contact.id == contact_pk)
                .values(avatar_id=avatar_pk, avatar_legacy_id=avatar_legacy_id)
            )
            session.commit()

    def get_avatar_legacy_id(self, contact_pk: int) -> Optional[str]:
        with self.session() as session:
            contact = session.execute(
                select(Contact).where(Contact.id == contact_pk)
            ).scalar()
            if contact is None or contact.avatar is None:
                return None
            return contact.avatar_legacy_id

    def update(self, contact: "LegacyContact", commit=True) -> int:
        with self.session() as session:
            if contact.contact_pk is None:
                if contact.cached_presence is not None:
                    presence_kwargs = contact.cached_presence._asdict()
                    presence_kwargs["cached_presence"] = True
                else:
                    presence_kwargs = {}
                row = Contact(
                    jid=contact.jid.bare,
                    legacy_id=str(contact.legacy_id),
                    user_account_id=contact.user_pk,
                    **presence_kwargs,
                )
            else:
                row = (
                    session.query(Contact)
                    .filter(Contact.id == contact.contact_pk)
                    .one()
                )
            row.nick = contact.name
            row.is_friend = contact.is_friend
            row.added_to_roster = contact.added_to_roster
            row.updated = True
            row.extra_attributes = contact.serialize_extra_attributes()
            row.caps_ver = contact._caps_ver
            row.vcard = contact._vcard
            row.vcard_fetched = contact._vcard_fetched
            row.client_type = contact.client_type
            session.add(row)
            if commit:
                session.commit()
            return row.id

    def set_vcard(self, contact_pk: int, vcard: str | None) -> None:
        with self.session() as session:
            session.execute(
                update(Contact)
                .where(Contact.id == contact_pk)
                .values(vcard=vcard, vcard_fetched=True)
            )
            session.commit()

    def add_to_sent(self, contact_pk: int, msg_id: str) -> None:
        with self.session() as session:
            if (
                session.query(ContactSent.id)
                .where(ContactSent.contact_id == contact_pk)
                .where(ContactSent.msg_id == msg_id)
                .first()
            ) is not None:
                log.warning(
                    "Contact %s has already sent message %s", contact_pk, msg_id
                )
                return
            new = ContactSent(contact_id=contact_pk, msg_id=msg_id)
            session.add(new)
            session.commit()

    def pop_sent_up_to(self, contact_pk: int, msg_id: str) -> list[str]:
        result = []
        to_del = []
        with self.session() as session:
            for row in session.execute(
                select(ContactSent)
                .where(ContactSent.contact_id == contact_pk)
                .order_by(ContactSent.id)
            ).scalars():
                to_del.append(row.id)
                result.append(row.msg_id)
                if row.msg_id == msg_id:
                    break
            for row_id in to_del:
                session.execute(delete(ContactSent).where(ContactSent.id == row_id))
        return result

    def set_friend(self, contact_pk: int, is_friend: bool) -> None:
        with self.session() as session:
            session.execute(
                update(Contact)
                .where(Contact.id == contact_pk)
                .values(is_friend=is_friend)
            )
            session.commit()

    def set_added_to_roster(self, contact_pk: int, value: bool) -> None:
        with self.session() as session:
            session.execute(
                update(Contact)
                .where(Contact.id == contact_pk)
                .values(added_to_roster=value)
            )
            session.commit()

    def delete(self, contact_pk: int) -> None:
        with self.session() as session:
            session.execute(delete(Contact).where(Contact.id == contact_pk))
            session.commit()

    def set_client_type(self, contact_pk: int, value: ClientType):
        with self.session() as session:
            session.execute(
                update(Contact)
                .where(Contact.id == contact_pk)
                .values(client_type=value)
            )
            session.commit()


class MAMStore(EngineMixin):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        with self.session() as session:
            session.execute(
                update(ArchivedMessage).values(source=ArchivedMessageSource.BACKFILL)
            )
            session.commit()

    def nuke_older_than(self, days: int) -> None:
        with self.session() as session:
            session.execute(
                delete(ArchivedMessage).where(
                    ArchivedMessage.timestamp < datetime.now() - timedelta(days=days)
                )
            )
            session.commit()

    def add_message(
        self,
        room_pk: int,
        message: HistoryMessage,
        archive_only: bool,
        legacy_msg_id: str | None,
    ) -> None:
        with self.session() as session:
            source = (
                ArchivedMessageSource.BACKFILL
                if archive_only
                else ArchivedMessageSource.LIVE
            )
            existing = session.execute(
                select(ArchivedMessage)
                .where(ArchivedMessage.room_id == room_pk)
                .where(ArchivedMessage.stanza_id == message.id)
            ).scalar()
            if existing is None and legacy_msg_id is not None:
                existing = session.execute(
                    select(ArchivedMessage)
                    .where(ArchivedMessage.room_id == room_pk)
                    .where(ArchivedMessage.legacy_id == legacy_msg_id)
                ).scalar()
            if existing is not None:
                log.debug("Updating message %s in room %s", message.id, room_pk)
                existing.timestamp = message.when
                existing.stanza = str(message.stanza)
                existing.author_jid = message.stanza.get_from()
                existing.source = source
                existing.legacy_id = legacy_msg_id
                session.add(existing)
                session.commit()
                return
            mam_msg = ArchivedMessage(
                stanza_id=message.id,
                timestamp=message.when,
                stanza=str(message.stanza),
                author_jid=message.stanza.get_from(),
                room_id=room_pk,
                source=source,
                legacy_id=legacy_msg_id,
            )
            session.add(mam_msg)
            session.commit()

    def get_messages(
        self,
        room_pk: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        before_id: Optional[str] = None,
        after_id: Optional[str] = None,
        ids: Collection[str] = (),
        last_page_n: Optional[int] = None,
        sender: Optional[str] = None,
        flip=False,
    ) -> Iterator[HistoryMessage]:

        with self.session() as session:
            q = select(ArchivedMessage).where(ArchivedMessage.room_id == room_pk)
            if start_date is not None:
                q = q.where(ArchivedMessage.timestamp >= start_date)
            if end_date is not None:
                q = q.where(ArchivedMessage.timestamp <= end_date)
            if before_id is not None:
                stamp = session.execute(
                    select(ArchivedMessage.timestamp).where(
                        ArchivedMessage.stanza_id == before_id
                    )
                ).scalar()
                if stamp is None:
                    raise XMPPError(
                        "item-not-found",
                        f"Message {before_id} not found",
                    )
                q = q.where(ArchivedMessage.timestamp < stamp)
            if after_id is not None:
                stamp = session.execute(
                    select(ArchivedMessage.timestamp).where(
                        ArchivedMessage.stanza_id == after_id
                    )
                ).scalar()
                if stamp is None:
                    raise XMPPError(
                        "item-not-found",
                        f"Message {after_id} not found",
                    )
                q = q.where(ArchivedMessage.timestamp > stamp)
            if ids:
                q = q.filter(ArchivedMessage.stanza_id.in_(ids))
            if sender is not None:
                q = q.where(ArchivedMessage.author_jid == sender)
            if flip:
                q = q.order_by(ArchivedMessage.timestamp.desc())
            else:
                q = q.order_by(ArchivedMessage.timestamp.asc())
            msgs = list(session.execute(q).scalars())
            if ids and len(msgs) != len(ids):
                raise XMPPError(
                    "item-not-found",
                    "One of the requested messages IDs could not be found "
                    "with the given constraints.",
                )
            if last_page_n is not None:
                if flip:
                    msgs = msgs[:last_page_n]
                else:
                    msgs = msgs[-last_page_n:]
            for h in msgs:
                yield HistoryMessage(
                    stanza=str(h.stanza), when=h.timestamp.replace(tzinfo=timezone.utc)
                )

    def get_first(self, room_pk: int, with_legacy_id=False) -> ArchivedMessage | None:
        with self.session() as session:
            q = (
                select(ArchivedMessage)
                .where(ArchivedMessage.room_id == room_pk)
                .order_by(ArchivedMessage.timestamp.asc())
            )
            if with_legacy_id:
                q = q.filter(ArchivedMessage.legacy_id.isnot(None))
            return session.execute(q).scalar()

    def get_last(
        self, room_pk: int, source: ArchivedMessageSource | None = None
    ) -> ArchivedMessage | None:
        with self.session() as session:
            q = select(ArchivedMessage).where(ArchivedMessage.room_id == room_pk)

            if source is not None:
                q = q.where(ArchivedMessage.source == source)

            return session.execute(
                q.order_by(ArchivedMessage.timestamp.desc())
            ).scalar()

    def get_first_and_last(self, room_pk: int) -> list[MamMetadata]:
        r = []
        with self.session():
            first = self.get_first(room_pk)
            if first is not None:
                r.append(MamMetadata(first.stanza_id, first.timestamp))
            last = self.get_last(room_pk)
            if last is not None:
                r.append(MamMetadata(last.stanza_id, last.timestamp))
        return r

    def get_most_recent_with_legacy_id(
        self, room_pk: int, source: ArchivedMessageSource | None = None
    ) -> ArchivedMessage | None:
        with self.session() as session:
            q = (
                select(ArchivedMessage)
                .where(ArchivedMessage.room_id == room_pk)
                .where(ArchivedMessage.legacy_id.isnot(None))
            )
            if source is not None:
                q = q.where(ArchivedMessage.source == source)
            return session.execute(
                q.order_by(ArchivedMessage.timestamp.desc())
            ).scalar()

    def get_least_recent_with_legacy_id_after(
        self, room_pk: int, after_id: str, source=ArchivedMessageSource.LIVE
    ) -> ArchivedMessage | None:
        with self.session() as session:
            after_timestamp = (
                session.query(ArchivedMessage.timestamp)
                .filter(ArchivedMessage.room_id == room_pk)
                .filter(ArchivedMessage.legacy_id == after_id)
                .scalar()
            )
            q = (
                select(ArchivedMessage)
                .where(ArchivedMessage.room_id == room_pk)
                .where(ArchivedMessage.legacy_id.isnot(None))
                .where(ArchivedMessage.source == source)
                .where(ArchivedMessage.timestamp > after_timestamp)
            )
            return session.execute(q.order_by(ArchivedMessage.timestamp.asc())).scalar()

    def get_by_legacy_id(self, room_pk: int, legacy_id: str) -> ArchivedMessage | None:
        with self.session() as session:
            return (
                session.query(ArchivedMessage)
                .filter(ArchivedMessage.room_id == room_pk)
                .filter(ArchivedMessage.legacy_id == legacy_id)
                .first()
            )


class MultiStore(EngineMixin):
    def get_xmpp_ids(self, user_pk: int, xmpp_id: str) -> list[str]:
        with self.session() as session:
            multi = session.execute(
                select(XmppIdsMulti)
                .where(XmppIdsMulti.xmpp_id == xmpp_id)
                .where(XmppIdsMulti.user_account_id == user_pk)
            ).scalar()
            if multi is None:
                return []
            return [m.xmpp_id for m in multi.legacy_ids_multi.xmpp_ids]

    def set_xmpp_ids(
        self, user_pk: int, legacy_msg_id: str, xmpp_ids: list[str], fail=False
    ) -> None:
        with self.session() as session:
            existing = session.execute(
                select(LegacyIdsMulti)
                .where(LegacyIdsMulti.user_account_id == user_pk)
                .where(LegacyIdsMulti.legacy_id == legacy_msg_id)
            ).scalar()
            if existing is not None:
                if fail:
                    raise
                log.debug("Resetting multi for %s", legacy_msg_id)
                session.execute(
                    delete(LegacyIdsMulti)
                    .where(LegacyIdsMulti.user_account_id == user_pk)
                    .where(LegacyIdsMulti.legacy_id == legacy_msg_id)
                )
                for i in xmpp_ids:
                    session.execute(
                        delete(XmppIdsMulti)
                        .where(XmppIdsMulti.user_account_id == user_pk)
                        .where(XmppIdsMulti.xmpp_id == i)
                    )
                session.commit()
                self.set_xmpp_ids(user_pk, legacy_msg_id, xmpp_ids, True)
                return

            row = LegacyIdsMulti(
                user_account_id=user_pk,
                legacy_id=legacy_msg_id,
                xmpp_ids=[
                    XmppIdsMulti(user_account_id=user_pk, xmpp_id=i)
                    for i in xmpp_ids
                    if i
                ],
            )
            session.add(row)
            session.commit()

    def get_legacy_id(self, user_pk: int, xmpp_id: str) -> Optional[str]:
        with self.session() as session:
            multi = session.execute(
                select(XmppIdsMulti)
                .where(XmppIdsMulti.xmpp_id == xmpp_id)
                .where(XmppIdsMulti.user_account_id == user_pk)
            ).scalar()
            if multi is None:
                return None
            return multi.legacy_ids_multi.legacy_id


class AttachmentStore(EngineMixin):
    def get_url(self, legacy_file_id: str) -> Optional[str]:
        with self.session() as session:
            return session.execute(
                select(Attachment.url).where(
                    Attachment.legacy_file_id == legacy_file_id
                )
            ).scalar()

    def set_url(self, user_pk: int, legacy_file_id: str, url: str) -> None:
        with self.session() as session:
            att = session.execute(
                select(Attachment)
                .where(Attachment.legacy_file_id == legacy_file_id)
                .where(Attachment.user_account_id == user_pk)
            ).scalar()
            if att is None:
                att = Attachment(
                    legacy_file_id=legacy_file_id, url=url, user_account_id=user_pk
                )
                session.add(att)
            else:
                att.url = url
            session.commit()

    def get_sims(self, url: str) -> Optional[str]:
        with self.session() as session:
            return session.execute(
                select(Attachment.sims).where(Attachment.url == url)
            ).scalar()

    def set_sims(self, url: str, sims: str) -> None:
        with self.session() as session:
            session.execute(
                update(Attachment).where(Attachment.url == url).values(sims=sims)
            )
            session.commit()

    def get_sfs(self, url: str) -> Optional[str]:
        with self.session() as session:
            return session.execute(
                select(Attachment.sfs).where(Attachment.url == url)
            ).scalar()

    def set_sfs(self, url: str, sfs: str) -> None:
        with self.session() as session:
            session.execute(
                update(Attachment).where(Attachment.url == url).values(sfs=sfs)
            )
            session.commit()

    def remove(self, legacy_file_id: str) -> None:
        with self.session() as session:
            session.execute(
                delete(Attachment).where(Attachment.legacy_file_id == legacy_file_id)
            )
            session.commit()


class RoomStore(UpdatedMixin):
    model = Room

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        with self.session() as session:
            session.execute(
                update(Room).values(
                    subject_setter=None,
                    user_resources=None,
                    history_filled=False,
                    participants_filled=False,
                )
            )
            session.commit()

    def set_avatar(
        self, room_pk: int, avatar_pk: int | None, avatar_legacy_id: str | None
    ) -> None:
        with self.session() as session:
            session.execute(
                update(Room)
                .where(Room.id == room_pk)
                .values(avatar_id=avatar_pk, avatar_legacy_id=avatar_legacy_id)
            )
            session.commit()

    def get_avatar_legacy_id(self, room_pk: int) -> Optional[str]:
        with self.session() as session:
            room = session.execute(select(Room).where(Room.id == room_pk)).scalar()
            if room is None or room.avatar is None:
                return None
            return room.avatar_legacy_id

    def get_by_jid(self, user_pk: int, jid: JID) -> Optional[Room]:
        if jid.resource:
            raise TypeError
        with self.session() as session:
            return session.execute(
                select(Room)
                .where(Room.user_account_id == user_pk)
                .where(Room.jid == jid)
            ).scalar()

    def get_by_legacy_id(self, user_pk: int, legacy_id: str) -> Optional[Room]:
        with self.session() as session:
            return session.execute(
                select(Room)
                .where(Room.user_account_id == user_pk)
                .where(Room.legacy_id == legacy_id)
            ).scalar()

    def update_subject_setter(self, room_pk: int, subject_setter: str | None):
        with self.session() as session:
            session.execute(
                update(Room)
                .where(Room.id == room_pk)
                .values(subject_setter=subject_setter)
            )
            session.commit()

    def update(self, muc: "LegacyMUC") -> int:
        with self.session() as session:
            if muc.pk is None:
                row = Room(
                    jid=muc.jid,
                    legacy_id=str(muc.legacy_id),
                    user_account_id=muc.user_pk,
                )
            else:
                row = session.query(Room).filter(Room.id == muc.pk).one()

            row.updated = True
            row.extra_attributes = muc.serialize_extra_attributes()
            row.name = muc.name
            row.description = muc.description
            row.user_resources = (
                None
                if not muc._user_resources
                else json.dumps(list(muc._user_resources))
            )
            row.muc_type = muc.type
            row.subject = muc.subject
            row.subject_date = muc.subject_date
            row.subject_setter = muc.subject_setter
            row.participants_filled = muc._participants_filled
            row.n_participants = muc._n_participants
            row.user_nick = muc.user_nick
            session.add(row)
            session.commit()
            return row.id

    def update_subject_date(
        self, room_pk: int, subject_date: Optional[datetime]
    ) -> None:
        with self.session() as session:
            session.execute(
                update(Room).where(Room.id == room_pk).values(subject_date=subject_date)
            )
            session.commit()

    def update_subject(self, room_pk: int, subject: Optional[str]) -> None:
        with self.session() as session:
            session.execute(
                update(Room).where(Room.id == room_pk).values(subject=subject)
            )
            session.commit()

    def update_description(self, room_pk: int, desc: Optional[str]) -> None:
        with self.session() as session:
            session.execute(
                update(Room).where(Room.id == room_pk).values(description=desc)
            )
            session.commit()

    def update_name(self, room_pk: int, name: Optional[str]) -> None:
        with self.session() as session:
            session.execute(update(Room).where(Room.id == room_pk).values(name=name))
            session.commit()

    def update_n_participants(self, room_pk: int, n: Optional[int]) -> None:
        with self.session() as session:
            session.execute(
                update(Room).where(Room.id == room_pk).values(n_participants=n)
            )
            session.commit()

    def update_user_nick(self, room_pk, nick: str) -> None:
        with self.session() as session:
            session.execute(
                update(Room).where(Room.id == room_pk).values(user_nick=nick)
            )
            session.commit()

    def delete(self, room_pk: int) -> None:
        with self.session() as session:
            session.execute(delete(Room).where(Room.id == room_pk))
            session.execute(delete(Participant).where(Participant.room_id == room_pk))
            session.commit()

    def set_resource(self, room_pk: int, resources: set[str]) -> None:
        with self.session() as session:
            session.execute(
                update(Room)
                .where(Room.id == room_pk)
                .values(
                    user_resources=(
                        None if not resources else json.dumps(list(resources))
                    )
                )
            )
            session.commit()

    def nickname_is_available(self, room_pk: int, nickname: str) -> bool:
        with self.session() as session:
            return (
                session.execute(
                    select(Participant)
                    .where(Participant.room_id == room_pk)
                    .where(Participant.nickname == nickname)
                ).scalar()
                is None
            )

    def set_participants_filled(self, room_pk: int, val=True) -> None:
        with self.session() as session:
            session.execute(
                update(Room).where(Room.id == room_pk).values(participants_filled=val)
            )
            session.commit()

    def set_history_filled(self, room_pk: int, val=True) -> None:
        with self.session() as session:
            session.execute(
                update(Room).where(Room.id == room_pk).values(history_filled=True)
            )
            session.commit()

    def get_all(self, user_pk: int) -> Iterator[Room]:
        with self.session() as session:
            yield from session.execute(
                select(Room).where(Room.user_account_id == user_pk)
            ).scalars()

    def get_all_jid_and_names(self, user_pk: int) -> Iterator[Room]:
        with self.session() as session:
            yield from session.scalars(
                select(Room)
                .filter(Room.user_account_id == user_pk)
                .options(load_only(Room.jid, Room.name))
                .order_by(Room.name)
            ).all()


class ParticipantStore(EngineMixin):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        with self.session() as session:
            session.execute(delete(participant_hats))
            session.execute(delete(Hat))
            session.execute(delete(Participant))
            session.commit()

    def add(self, room_pk: int, nickname: str) -> int:
        with self.session() as session:
            existing = session.execute(
                select(Participant.id)
                .where(Participant.room_id == room_pk)
                .where(Participant.nickname == nickname)
            ).scalar()
            if existing is not None:
                return existing
            participant = Participant(room_id=room_pk, nickname=nickname)
            session.add(participant)
            session.commit()
            return participant.id

    def get_by_nickname(self, room_pk: int, nickname: str) -> Optional[Participant]:
        with self.session() as session:
            return session.execute(
                select(Participant)
                .where(Participant.room_id == room_pk)
                .where(Participant.nickname == nickname)
            ).scalar()

    def get_by_resource(self, room_pk: int, resource: str) -> Optional[Participant]:
        with self.session() as session:
            return session.execute(
                select(Participant)
                .where(Participant.room_id == room_pk)
                .where(Participant.resource == resource)
            ).scalar()

    def get_by_contact(self, room_pk: int, contact_pk: int) -> Optional[Participant]:
        with self.session() as session:
            return session.execute(
                select(Participant)
                .where(Participant.room_id == room_pk)
                .where(Participant.contact_id == contact_pk)
            ).scalar()

    def get_all(self, room_pk: int, user_included=True) -> Iterator[Participant]:
        with self.session() as session:
            q = select(Participant).where(Participant.room_id == room_pk)
            if not user_included:
                q = q.where(~Participant.is_user)
            yield from session.execute(q).scalars()

    def get_for_contact(self, contact_pk: int) -> Iterator[Participant]:
        with self.session() as session:
            yield from session.execute(
                select(Participant).where(Participant.contact_id == contact_pk)
            ).scalars()

    def update(self, participant: "LegacyParticipant") -> None:
        with self.session() as session:
            session.execute(
                update(Participant)
                .where(Participant.id == participant.pk)
                .values(
                    resource=participant.jid.resource,
                    affiliation=participant.affiliation,
                    role=participant.role,
                    presence_sent=participant._presence_sent,  # type:ignore
                    # hats=[self.add_hat(h.uri, h.title) for h in participant._hats],
                    is_user=participant.is_user,
                    contact_id=(
                        None
                        if participant.contact is None
                        else participant.contact.contact_pk
                    ),
                )
            )
            session.commit()

    def add_hat(self, uri: str, title: str) -> Hat:
        with self.session() as session:
            existing = session.execute(
                select(Hat).where(Hat.uri == uri).where(Hat.title == title)
            ).scalar()
            if existing is not None:
                return existing
            hat = Hat(uri=uri, title=title)
            session.add(hat)
            session.commit()
            return hat

    def set_presence_sent(self, participant_pk: int) -> None:
        with self.session() as session:
            session.execute(
                update(Participant)
                .where(Participant.id == participant_pk)
                .values(presence_sent=True)
            )
            session.commit()

    def set_affiliation(self, participant_pk: int, affiliation: MucAffiliation) -> None:
        with self.session() as session:
            session.execute(
                update(Participant)
                .where(Participant.id == participant_pk)
                .values(affiliation=affiliation)
            )
            session.commit()

    def set_role(self, participant_pk: int, role: MucRole) -> None:
        with self.session() as session:
            session.execute(
                update(Participant)
                .where(Participant.id == participant_pk)
                .values(role=role)
            )
            session.commit()

    def set_hats(self, participant_pk: int, hats: list[HatTuple]) -> None:
        with self.session() as session:
            part = session.execute(
                select(Participant).where(Participant.id == participant_pk)
            ).scalar()
            if part is None:
                raise ValueError
            part.hats.clear()
            for h in hats:
                hat = self.add_hat(*h)
                if hat in part.hats:
                    continue
                part.hats.append(hat)
            session.commit()

    def delete(self, participant_pk: int) -> None:
        with self.session() as session:
            session.execute(delete(Participant).where(Participant.id == participant_pk))

    def get_count(self, room_pk: int) -> int:
        with self.session() as session:
            return session.query(
                count(Participant.id).filter(Participant.room_id == room_pk)
            ).scalar()


class BobStore(EngineMixin):
    _ATTR_MAP = {
        "sha-1": "sha_1",
        "sha1": "sha_1",
        "sha-256": "sha_256",
        "sha256": "sha_256",
        "sha-512": "sha_512",
        "sha512": "sha_512",
    }

    _ALG_MAP = {
        "sha_1": hashlib.sha1,
        "sha_256": hashlib.sha256,
        "sha_512": hashlib.sha512,
    }

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.root_dir = config.HOME_DIR / "slidge_stickers"
        self.root_dir.mkdir(exist_ok=True)

    @staticmethod
    def __split_cid(cid: str) -> list[str]:
        return cid.removesuffix("@bob.xmpp.org").split("+")

    def __get_condition(self, cid: str):
        alg_name, digest = self.__split_cid(cid)
        attr = self._ATTR_MAP.get(alg_name)
        if attr is None:
            log.warning("Unknown hash algo: %s", alg_name)
            return None
        return getattr(Bob, attr) == digest

    def get(self, cid: str) -> Bob | None:
        with self.session() as session:
            try:
                return session.query(Bob).filter(self.__get_condition(cid)).scalar()
            except ValueError:
                log.warning("Cannot get Bob with CID: %s", cid)
                return None

    def get_sticker(self, cid: str) -> Sticker | None:
        bob = self.get(cid)
        if bob is None:
            return None
        return Sticker(
            self.root_dir / bob.file_name,
            bob.content_type,
            {h: getattr(bob, h) for h in self._ALG_MAP},
        )

    def get_bob(self, _jid, _node, _ifrom, cid: str) -> BitsOfBinary | None:
        stored = self.get(cid)
        if stored is None:
            return None
        bob = BitsOfBinary()
        bob["data"] = (self.root_dir / stored.file_name).read_bytes()
        if stored.content_type is not None:
            bob["type"] = stored.content_type
        bob["cid"] = cid
        return bob

    def del_bob(self, _jid, _node, _ifrom, cid: str) -> None:
        with self.session() as orm:
            try:
                file_name = orm.scalar(
                    delete(Bob)
                    .where(self.__get_condition(cid))
                    .returning(Bob.file_name)
                )
            except ValueError:
                log.warning("Cannot delete Bob with CID: %s", cid)
                return None
            if file_name is None:
                log.warning("No BoB with CID: %s", cid)
                return None
            (self.root_dir / file_name).unlink()
            orm.commit()

    def set_bob(self, _jid, _node, _ifrom, bob: BitsOfBinary) -> None:
        cid = bob["cid"]
        try:
            alg_name, digest = self.__split_cid(cid)
        except ValueError:
            log.warning("Cannot set Bob with CID: %s", cid)
            return
        attr = self._ATTR_MAP.get(alg_name)
        if attr is None:
            log.warning("Cannot set BoB with unknown hash algo: %s", alg_name)
            return None
        with self.session() as orm:
            existing = self.get(bob["cid"])
            if existing is not None:
                log.debug("Bob already known")
                return
            bytes_ = bob["data"]
            path = self.root_dir / uuid.uuid4().hex
            if bob["type"]:
                path = path.with_suffix(guess_extension(bob["type"]) or "")
            path.write_bytes(bytes_)
            hashes = {k: v(bytes_).hexdigest() for k, v in self._ALG_MAP.items()}
            if hashes[attr] != digest:
                raise ValueError(
                    "The given CID does not correspond to the result of our hash"
                )
            row = Bob(file_name=path.name, content_type=bob["type"] or None, **hashes)
            orm.add(row)
            orm.commit()


log = logging.getLogger(__name__)
_session: Optional[Session] = None
