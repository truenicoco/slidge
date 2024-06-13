from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from slixmpp import JID, Iq, Message, Presence
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, attributes

from ..util.sql import db
from ..util.types import URL
from .models import Avatar, GatewayUser


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


class UserStore(EngineMixin):
    def new(self, jid: JID, legacy_module_data: dict) -> GatewayUser:
        if jid.resource:
            jid = JID(jid.bare)
        user = GatewayUser(jid=jid, legacy_module_data=legacy_module_data)
        with self.session(expire_on_commit=False) as session:
            session.add(user)
            session.commit()
        db.user_store(user.jid)  # TODO: remove this temporary SQLite db nonsense
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

    def get_by_jid(self, jid: JID) -> Optional[Avatar]:
        with self.session() as session:
            return session.execute(select(Avatar).where(Avatar.jid == jid)).scalar()
