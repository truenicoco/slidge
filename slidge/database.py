import typing
import logging
from copy import deepcopy

from slixmpp import JID

import sqlalchemy.types as types
from sqlalchemy import create_engine, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import sessionmaker, relationship

Base = declarative_base()


class BareJIDType(types.TypeDecorator):
    impl = String

    def process_bind_param(self, value: typing.Union[JID, str], dialect):
        if isinstance(value, JID):
            return value.bare
        else:
            return value

    def process_result_value(self, value: str, dialect):
        return JID(value)


# TODO: define table dynamically based on Gateway registration form fields
class User(Base):
    """
    The gateway user
    """

    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    jid = Column(BareJIDType, nullable=False)
    legacy_id = Column(String, nullable=False)
    legacy_password = Column(String)

    def __repr__(self):
        return f"<User {self.jid} ({self.legacy_id})>"

    @staticmethod
    def by_jid(jid: JID) -> typing.Union["User", None]:
        """Return a user by its JID"""
        return session.query(User).filter(User.jid == jid.bare).one_or_none()

    @staticmethod
    def by_legacy_id(legacy_id: str) -> typing.Union["User", None]:
        """Return a user by its legacy ID"""
        return session.query(User).filter(User.legacy_id == legacy_id).one_or_none()

    def commit(self):
        session.add(self)
        session.commit()

    def delete(self):
        session.delete(self)

    @classmethod
    def all(cls):
        return session.query(cls)


class RosterEntry(Base):
    __tablename__ = "roster"
    id = Column(Integer, primary_key=True)
    owner_jid = Column(BareJIDType, nullable=False)
    jid = Column(BareJIDType, nullable=False)
    from_ = Column(types.Boolean, nullable=False)
    to = Column(types.Boolean, nullable=False)
    pending_in = Column(types.Boolean, nullable=False)
    pending_out = Column(types.Boolean, nullable=False)
    whitelisted = Column(types.Boolean, nullable=False)
    subscription = Column(String, nullable=False)

    name = Column(String, nullable=False, default="")
    removed = Column(types.Boolean, default=False)

    @staticmethod
    def by_id(id) -> "RosterEntry":
        return session.query(RosterEntry).filter(RosterEntry.id == id).one()

    def to_dict(self) -> dict:
        item_state = deepcopy(self.__dict__)
        item_state["from"] = item_state.pop("from_")
        item_state["groups"] = []
        for attr in ["_sa_instance_state", "id", "jid", "owner_jid"]:
            item_state.pop(attr)
        return item_state

    def update_from_dict(self, item_state):
        for k, v in item_state.items():
            setattr(self, k, v)

class RosterBackend:
    def __init__(self, gateway_jid):
        self.gateway_jid = gateway_jid

    def entries(self, owner_jid, default=None):
        # TODO: fix this mess without breaking everythin
        if owner_jid is None:
            return set(e.owner_jid for e in session.query(RosterEntry).all())
        else:
            return set(
                e.jid
                for e in session.query(RosterEntry)
                .filter(RosterEntry.owner_jid == JID(owner_jid))
                .all()
            )

    def save(self, owner_jid, jid, item_state, db_state):
        if owner_jid != self.gateway_jid:
            # No need to store legacy contact roster, they only have
            # the corresponding jabber user with sub=both in them…
            # FIXME: after consideration, we should store contacts of legacy users
            # because of this race condition: user launches his XMPP client, sends probes
            # to his legacy buddies, gateway replies unsub for all of them before
            # pushing them again to the user's roster, resulting in unwanted noise
            return
        item_state = deepcopy(item_state)
        item_state["from_"] = item_state.pop("from")
        item_state.pop("groups")
        id = db_state.get("id")
        if id is None:
            entry = RosterEntry(jid=jid, owner_jid=owner_jid, **item_state)
            session.add(entry)
        else:
            entry = RosterEntry.by_id(id)
            entry.update_from_dict(item_state)
        session.commit()
        db_state["id"] = entry.id

    def load(self, owner_jid, jid, db_state):
        id = db_state.get("id")
        if id is None:
            entry = (
                session.query(RosterEntry)
                .filter(
                    (RosterEntry.owner_jid == JID(owner_jid))
                    & (RosterEntry.jid == JID(jid))
                )
                .one_or_none()
            )
            if entry is None:
                return {}
        else:
            entry = RosterEntry.by_id(id)
        db_state["id"] = entry.id
        return entry.to_dict()


def init_session(sql_path: str, echo=True):
    """
    Called to initialize the DB engine.
    """
    global session
    engine = create_engine(sql_path, echo=echo)
    Base.metadata.create_all(engine)
    Session.configure(bind=engine)
    session = Session()


Session = sessionmaker()
session = None

log = logging.getLogger(__name__)
