from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from slixmpp import JID, Iq, Message, Presence
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, attributes

from ..util.sql import db
from .models import GatewayUser


class EngineMixin:
    def __init__(self, engine: Engine):
        self._engine = engine

    @contextmanager
    def _session(self, **session_kwargs) -> Iterator[Session]:
        with Session(self._engine, **session_kwargs) as session:
            yield session


class SlidgeStore:
    def __init__(self, engine: Engine):
        self.users = UserStore(engine)


class UserStore(EngineMixin):
    def new(self, jid: JID, legacy_module_data: dict) -> GatewayUser:
        if jid.resource:
            jid = JID(jid.bare)
        user = GatewayUser(jid=jid, legacy_module_data=legacy_module_data)
        with self._session(expire_on_commit=False) as session:
            session.add(user)
            session.commit()
        db.user_store(user.jid)  # TODO: remove this temporary SQLite db nonsense
        return user

    def update(self, user: GatewayUser):
        # https://github.com/sqlalchemy/sqlalchemy/discussions/6473
        attributes.flag_modified(user, "legacy_module_data")
        attributes.flag_modified(user, "preferences")
        with self._session() as session:
            session.add(user)
            session.commit()

    def get_all(self) -> Iterator[GatewayUser]:
        with self._session() as session:
            yield from session.execute(select(GatewayUser)).scalars()

    def get(self, jid: JID) -> Optional[GatewayUser]:
        with self._session() as session:
            return session.execute(
                select(GatewayUser).where(GatewayUser.jid == jid.bare)
            ).scalar()

    def get_by_stanza(self, stanza: Iq | Message | Presence) -> Optional[GatewayUser]:
        return self.get(stanza.get_from())

    def delete(self, jid: JID) -> None:
        with self._session() as session:
            session.delete(self.get(jid))
            session.commit()
