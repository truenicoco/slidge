import logging
from dataclasses import dataclass, field
import typing

from slixmpp import JID, Message, Iq, Presence, ComponentXMPP

from slidge.buddy import Buddy, Buddies
from slidge.muc import LegacyMucList, LegacyMuc
from slidge.base_legacy import BaseLegacyClient
from slidge.database import User


class Session:
    """
    Represents a XMPP user session on the gateway and the legacy network.
    Should only be instantiated once per user, something done using :class:`Sessions`.
    """

    def __init__(self, xmpp, user: User):
        from slidge.gateway import BaseGateway

        self.user: User = user
        self.xmpp: BaseGateway = xmpp
        self.mucs = LegacyMucList()
        self.buddies = Buddies()
        self.mucs.xmpp = self.buddies.xmpp = self.xmpp
        self.mucs.user = self.buddies.user = self.user

        self.logging_in = False
        self.logged_in = False

    @property
    def legacy(self) -> BaseLegacyClient:
        return self.xmpp.legacy_client

    async def login(self):
        if self.logging_in or self.logged_in:
            return
        self.logging_in = True
        self.login_future = self.xmpp.loop.create_future()

        await self.legacy.login(self.user)
        await self.buddies.sync()
        await self.mucs.sync()
        self.xmpp["xep_0100"].send_presence(ptype="available", pto=self.user.jid)
        self.logging_in = False
        self.logged_in = True

        if self.xmpp.config["gateway"].getboolean("send-muc-invitations-on-connect"):
            self.mucs.send_invitations()

        self.login_future.set_result(True)

    async def logout(self):
        if self.logging_in:
            await self.login_future
        if self.logged_in:
            await self.legacy.logout(self.user)
            await self.mucs.shutdown()
            self.buddies.shutdown()
            self.xmpp["xep_0100"].send_presence(ptype="unavailable", pto=self.user.jid)
            self.logged_in = False


class Sessions:
    """
    Convenient class to have a single `Session` for each gateway user.
    """

    def __init__(self):
        self.xmpp: typing.Optional["BaseGateway"] = None
        self._sessions_by_user: typing.Dict[User, Session] = dict()
        self._sessions_by_jid: typing.Dict[JID, Session] = dict()
        self._sessions_by_legacy_id: typing.Dict[JID, Session] = dict()

    def __iter__(self):
        return iter(self._sessions_by_user.values())

    def __len__(self):
        return len(self._sessions_by_user.values())

    def __getitem__(self, user: User) -> Session:
        try:
            return self._sessions_by_user[user]
        except KeyError:
            session = Session(user=user, xmpp=self.xmpp)
            self._add(session)
            return session

    def _add(self, session: Session):
        self._sessions_by_user[session.user] = session
        self._sessions_by_jid[session.user.jid.bare] = session
        self._sessions_by_legacy_id[session.user.legacy_id] = session

    def by_jid(self, jid: JID) -> Session:
        """
        Return the session of an gateway user by its JID
        """
        try:
            return self._sessions_by_jid[jid.bare]
        except KeyError:
            user = User.by_jid(jid)
            if user is None:
                raise KeyError(f"Could not find user by JID: {jid}")
            session = Session(user=user, xmpp=self.xmpp)
            self._add(session)
            return session

    def by_legacy_id(self, legacy_id: str) -> Session:
        """
        Return the session of an gateway user by its legacy network id
        """
        try:
            return self._sessions_by_legacy_id[legacy_id]
        except KeyError:
            user = User.by_legacy_id(legacy_id)
            if user is None:
                raise KeyError(f"Could not find user by legacy ID: {legacy_id}")
            session = Session(user=user, xmpp=self.xmpp)
            self._add(session)
            return session

    def destroy_by_jid(self, user_jid: JID):
        log.debug(f"Destroying session for {user_jid}")
        try:
            session = self.by_jid(user_jid)
        except KeyError as e:
            log.error(f"Couldnt legacy logout for {user_jid}: {e}")
            return
        self.xmpp.loop.create_task(session.logout())
        del self._sessions_by_user[session.user]
        del self._sessions_by_jid[session.user.jid]
        del self._sessions_by_legacy_id[session.user.legacy_id]

    async def shutdown(self):
        for s in self:
            await s.logout()


sessions = Sessions()
log = logging.getLogger(__name__)