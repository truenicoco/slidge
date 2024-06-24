from __future__ import annotations

import logging
import warnings
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Collection, Iterator, Optional

from slixmpp import JID, Iq, Message, Presence
from slixmpp.exceptions import XMPPError
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, attributes

from ..util.archive_msg import HistoryMessage
from ..util.types import URL, CachedPresence, MamMetadata
from .models import (
    ArchivedMessage,
    Attachment,
    Avatar,
    Contact,
    GatewayUser,
    LegacyIdsMulti,
    Room,
    XmppIdsMulti,
    XmppToLegacyEnum,
    XmppToLegacyIds,
)


class EngineMixin:
    def __init__(self, engine: Engine):
        self._engine = engine
        self.__session: Optional[Session] = None

    @contextmanager
    def session(self, **session_kwargs) -> Iterator[Session]:
        if self.__session is not None:
            yield self.__session
            return
        with Session(self._engine, **session_kwargs) as session:
            self.__session = session
            yield session
            self.__session = None


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


class UserStore(EngineMixin):
    def new(self, jid: JID, legacy_module_data: dict) -> GatewayUser:
        if jid.resource:
            jid = JID(jid.bare)
        user = GatewayUser(jid=jid, legacy_module_data=legacy_module_data)
        with self.session(expire_on_commit=False) as session:
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
    def get_by_url(self, url: URL) -> Optional[Avatar]:
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


class ContactStore(EngineMixin):
    def __get_user_pk(self, user_jid: JID) -> int:
        with self.session() as session:
            return session.execute(
                select(GatewayUser.id).where(GatewayUser.jid == user_jid.bare)
            ).one()[0]

    def is_contact_of(self, contact_jid: JID, user_jid: JID) -> bool:
        with self.session() as session:
            return (
                session.execute(
                    select(Contact)
                    .where(Contact.jid == contact_jid.bare)
                    .where(Contact.user_account_id == self.__get_user_pk(user_jid))
                ).scalar()
                is not None
            )

    def add(self, user_pk: int, legacy_id: str, contact_jid: JID) -> int:
        with self.session() as session:
            contact = Contact(
                jid=contact_jid.bare, legacy_id=legacy_id, user_account_id=user_pk
            )
            session.add(contact)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                log.debug(
                    "Contact %s was already here for user %s", contact_jid, user_pk
                )
                return session.execute(
                    select(Contact.id)
                    .where(Contact.jid == contact_jid.bare)
                    .where(Contact.user_account_id == user_pk)
                ).one()[0]
            else:
                return contact.id

    def get(self, contact_jid: JID, user_jid: JID) -> Optional[Contact]:
        with self.session() as session:
            return session.execute(
                select(Contact)
                .where(Contact.jid == contact_jid.bare)
                .where(Contact.user_account_id == self.__get_user_pk(user_jid))
            ).scalar()

    def update_nick(self, contact_jid: JID, user_jid: JID, nick: Optional[str]) -> None:
        with self.session() as session:
            session.execute(
                update(Contact)
                .where(Contact.jid == contact_jid.bare)
                .where(Contact.user_account_id == self.__get_user_pk(user_jid))
                .values(nick=nick)
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
            mam_msg = ArchivedMessage(
                stanza_id=message.id,
                timestamp=message.when,
                stanza=str(message.stanza),
                author_jid=message.stanza.get_from(),
                room_id=room_pk,
            )
            session.add(mam_msg)
            try:
                session.commit()
            except IntegrityError as e:
                log.debug(
                    "Problem when trying to insert a message, updating instead",
                    exc_info=e,
                )
                session.rollback()
                session.execute(
                    update(ArchivedMessage)
                    .where(ArchivedMessage.room_id == room_pk)
                    .where(ArchivedMessage.stanza_id == message.id)
                    .values(
                        stanza_id=message.id,
                        timestamp=message.when,
                        stanza=str(message.stanza),
                        author_jid=message.stanza.get_from(),
                    )
                )
                session.commit()

    def get_messages(
        self,
        room_id: int,
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
            q = select(ArchivedMessage).where(ArchivedMessage.room_id == room_id)
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
            try:
                session.commit()
            except IntegrityError:
                if fail:
                    raise
                log.warning("Resetting multi for %s", legacy_msg_id)
                session.rollback()
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


class RoomStore(EngineMixin):
    def add(self, user_pk: int, legacy_id: str, jid: JID) -> int:
        with self.session() as session:
            room = Room(jid=jid, user_account_id=user_pk, legacy_id=legacy_id)
            session.add(room)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                log.debug("Room %s was already here for user %s", jid, user_pk)
                return session.execute(
                    select(Room.id)
                    .where(Room.legacy_id == legacy_id)
                    .where(Room.jid == jid)
                    .where(Room.user_account_id == user_pk)
                ).one()[0]
            else:
                return room.id


log = logging.getLogger(__name__)
