from __future__ import annotations

import json
import logging
import warnings
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Collection, Iterator, Optional, Type

from slixmpp import JID, Iq, Message, Presence
from slixmpp.exceptions import XMPPError
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session, attributes

from ..util.archive_msg import HistoryMessage
from ..util.types import URL, CachedPresence
from ..util.types import Hat as HatTuple
from ..util.types import MamMetadata, MucAffiliation, MucRole
from .meta import Base
from .models import (
    ArchivedMessage,
    Attachment,
    Avatar,
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


class AvatarStore(EngineMixin):
    def get_by_url(self, url: URL | str) -> Optional[Avatar]:
        with self.session() as session:
            return session.execute(select(Avatar).where(Avatar.url == url)).scalar()

    def get_by_legacy_id(self, legacy_id: str) -> Optional[Avatar]:
        with self.session() as session:
            return session.execute(
                select(Avatar).where(Avatar.legacy_id == legacy_id)
            ).scalar()

    def store_jid(self, sha: str, jid: JID) -> None:
        with self.session() as session:
            pk = session.execute(select(Avatar.id).where(Avatar.hash == sha)).scalar()
            if pk is None:
                warnings.warn("avatar not found")
                return
            session.execute(
                update(Contact).where(Contact.jid == jid.bare).values(avatar_id=pk)
            )
            session.execute(
                update(Room).where(Room.jid == jid.bare).values(avatar_id=pk)
            )
            session.commit()

    def get_by_jid(self, jid: JID) -> Optional[Avatar]:
        with self.session() as session:
            avatar = session.execute(
                select(Avatar).where(Avatar.contacts.any(Contact.jid == jid))
            ).scalar()
            if avatar is not None:
                return avatar
            return session.execute(
                select(Avatar).where(Avatar.rooms.any(Room.jid == jid))
            ).scalar()

    def get_by_pk(self, pk: int) -> Optional[Avatar]:
        with self.session() as session:
            return session.execute(select(Avatar).where(Avatar.id == pk)).scalar()


class SentStore(EngineMixin):
    def set_message(self, user_pk: int, legacy_id: str, xmpp_id: str) -> None:
        with self.session() as session:
            msg = XmppToLegacyIds(
                user_account_id=user_pk,
                legacy_id=legacy_id,
                xmpp_id=xmpp_id,
                type=XmppToLegacyEnum.DM,
            )
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

    def get_xmpp_thread(self, user_pk: int, legacy_id: str) -> Optional[str]:
        with self.session() as session:
            return session.execute(
                select(XmppToLegacyIds.xmpp_id)
                .where(XmppToLegacyIds.user_account_id == user_pk)
                .where(XmppToLegacyIds.legacy_id == legacy_id)
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

    def add(self, user_pk: int, legacy_id: str, contact_jid: JID) -> int:
        with self.session() as session:
            existing = session.execute(
                select(Contact)
                .where(Contact.legacy_id == legacy_id)
                .where(Contact.jid == contact_jid.bare)
            ).scalar()
            if existing is not None:
                return existing.id
            contact = Contact(
                jid=contact_jid.bare, legacy_id=legacy_id, user_account_id=user_pk
            )
            session.add(contact)
            session.commit()
            return contact.id

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

    def set_avatar(self, contact_pk: int, avatar_pk: Optional[int]):
        with self.session() as session:
            session.execute(
                update(Contact)
                .where(Contact.id == contact_pk)
                .values(avatar_id=avatar_pk)
            )
            session.commit()

    def get_avatar_legacy_id(self, contact_pk: int) -> Optional[str]:
        with self.session() as session:
            contact = session.execute(
                select(Contact).where(Contact.id == contact_pk)
            ).scalar()
            if contact is None or contact.avatar is None:
                return None
            return contact.avatar.legacy_id

    def update(self, contact: "LegacyContact"):
        with self.session() as session:
            session.execute(
                update(Contact)
                .where(Contact.id == contact.contact_pk)
                .values(
                    nick=contact.name,
                    is_friend=contact.is_friend,
                    added_to_roster=contact.added_to_roster,
                    updated=True,
                    extra_attributes=contact.serialize_extra_attributes(),
                )
            )
            session.commit()

    def add_to_sent(self, contact_pk: int, msg_id: str) -> None:
        with self.session() as session:
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


class MAMStore(EngineMixin):
    def nuke_older_than(self, days: int) -> None:
        with self.session() as session:
            session.execute(
                delete(ArchivedMessage).where(
                    ArchivedMessage.timestamp > datetime.now() - timedelta(days=days)
                )
            )
            session.commit()

    def add_message(self, room_pk: int, message: HistoryMessage) -> None:
        with self.session() as session:
            existing = session.execute(
                select(ArchivedMessage)
                .where(ArchivedMessage.room_id == room_pk)
                .where(ArchivedMessage.stanza_id == message.id)
            ).scalar()
            if existing is not None:
                log.debug("Updating message %s in room %s", message.id, room_pk)
                existing.timestamp = message.when
                existing.stanza = str(message.stanza)
                existing.author_jid = message.stanza.get_from()
                session.add(existing)
                session.commit()
                return
            mam_msg = ArchivedMessage(
                stanza_id=message.id,
                timestamp=message.when,
                stanza=str(message.stanza),
                author_jid=message.stanza.get_from(),
                room_id=room_pk,
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

    def get_first_and_last(self, room_pk: int) -> list[MamMetadata]:
        r = []
        with self.session() as session:
            first = session.execute(
                select(ArchivedMessage.stanza_id, ArchivedMessage.timestamp)
                .where(ArchivedMessage.room_id == room_pk)
                .order_by(ArchivedMessage.timestamp.asc())
            ).first()
            if first is not None:
                r.append(MamMetadata(*first))
            last = session.execute(
                select(ArchivedMessage.stanza_id, ArchivedMessage.timestamp)
                .where(ArchivedMessage.room_id == room_pk)
                .order_by(ArchivedMessage.timestamp.desc())
            ).first()
            if last is not None:
                r.append(MamMetadata(*last))
        return r


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
                log.warning("Resetting multi for %s", legacy_msg_id)
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
                update(Room).values(subject_setter_id=None, user_resources=None)
            )
            session.commit()

    def add(self, user_pk: int, legacy_id: str, jid: JID) -> int:
        if jid.resource:
            raise TypeError
        with self.session() as session:
            existing = session.execute(
                select(Room.id)
                .where(Room.user_account_id == user_pk)
                .where(Room.legacy_id == legacy_id)
            ).scalar()
            if existing is not None:
                return existing
            room = Room(jid=jid, user_account_id=user_pk, legacy_id=legacy_id)
            session.add(room)
            session.commit()
            return room.id

    def set_avatar(self, room_pk: int, avatar_pk: int) -> None:
        with self.session() as session:
            session.execute(
                update(Room).where(Room.id == room_pk).values(avatar_id=avatar_pk)
            )
            session.commit()

    def get_avatar_legacy_id(self, room_pk: int) -> Optional[str]:
        with self.session() as session:
            room = session.execute(select(Room).where(Room.id == room_pk)).scalar()
            if room is None or room.avatar is None:
                return None
            return room.avatar.legacy_id

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

    def update(self, room: "LegacyMUC"):
        from slidge.contact import LegacyContact

        with self.session() as session:
            if room.subject_setter is None:
                subject_setter_id = None
            elif isinstance(room.subject_setter, str):
                subject_setter_id = None
            elif isinstance(room.subject_setter, LegacyContact):
                subject_setter_id = None
            elif room.subject_setter.is_system:
                subject_setter_id = None
            else:
                subject_setter_id = room.subject_setter.pk

            session.execute(
                update(Room)
                .where(Room.id == room.pk)
                .values(
                    updated=True,
                    extra_attributes=room.serialize_extra_attributes(),
                    name=room.name,
                    description=room.description,
                    user_resources=(
                        None
                        if not room._user_resources
                        else json.dumps(list(room._user_resources))
                    ),
                    muc_type=room.type,
                    subject=room.subject,
                    subject_date=room.subject_date,
                    subject_setter_id=subject_setter_id,
                    participants_filled=room._participants_filled,
                )
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

    def get_all(self, user_pk: int) -> Iterator[Room]:
        with self.session() as session:
            yield from session.execute(
                select(Room).where(Room.user_account_id == user_pk)
            ).scalars()


class ParticipantStore(EngineMixin):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        with self.session() as session:
            session.execute(delete(Participant))
            session.execute(delete(Hat))
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


log = logging.getLogger(__name__)
_session: Optional[Session] = None
