import logging
from abc import ABC
from typing import List, Dict

from slixmpp import Presence, JID, Iq
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0100 import LegacyError

from ..gateway import BaseGateway
from ..db import GatewayUser, user_store
from .util import get_unique_subclass


class BaseLegacyClient(ABC):
    """
    Abstract base class for interacting with the legacy network
    """

    def __init__(self, xmpp: BaseGateway):
        """
        :param xmpp: The gateway, to interact with the XMPP network
        """
        from .session import BaseSession  # circular import hell

        self._session_cls = get_unique_subclass(BaseSession)
        self.xmpp = self._session_cls.xmpp = xmpp

        xmpp["xep_0077"].api.register(self._user_validate, "user_validate")
        xmpp.add_event_handler("user_unregister", self._on_user_unregister)

        get_session = self._session_cls.from_stanza

        # fmt: off
        async def logout(p): await get_session(p).logout(p)
        async def msg(m): await get_session(m).send_from_msg(m)
        async def disp(m): await get_session(m).displayed_from_msg(m)
        async def active(m): await get_session(m).active_from_msg(m)
        async def inactive(m): await get_session(m).inactive_from_msg(m)
        async def composing(m): await get_session(m).composing_from_msg(m)
        # fmt: on

        xmpp.add_event_handler("legacy_login", self.legacy_login)
        xmpp.add_event_handler("legacy_logout", logout)
        xmpp.add_event_handler("legacy_message", msg)
        self.xmpp.add_event_handler("marker_displayed", disp)
        self.xmpp.add_event_handler("chatstate_active", active)
        self.xmpp.add_event_handler("chatstate_inactive", inactive)
        self.xmpp.add_event_handler("chatstate_composing", composing)

    def config(self, argv: List[str]):
        """
        Override this to access CLI args to configure the slidge plugin

        :param argv: CLI args that were not parsed by Slidge
        """
        pass

    async def legacy_login(self, p: Presence):
        """
        Logs a :class:`.BaseSession` instance to the legacy network

        :param p: Presence from a :class:`.GatewayUser` directed at the gateway's own JID
        """
        session = self._session_cls.from_stanza(p)
        if not session.logged:
            session.logged = True
            await session.login(p)

    async def _user_validate(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        log.debug("User validate: %s", (ifrom.bare, iq))
        form = iq["register"]["form"].get_values()

        for field in self.xmpp.REGISTRATION_FIELDS:
            if field.required and not form.get(field.name):
                raise XMPPError("Please fill in all fields", etype="modify")

        form_dict = {f.name: form.get(f.name) for f in self.xmpp.REGISTRATION_FIELDS}

        try:
            await self.validate(ifrom, form_dict)
        except LegacyError as e:
            raise ValueError(f"Login Problem: {e}")
        else:
            user_store.add(ifrom, form)

    async def validate(self, user_jid: JID, registration_form: Dict[str, str]):
        """
        Validate a registration form from a user.

        Since :xep:`0077` is pretty limited in terms of validation, it is OK to validate
        anything that looks good here and continue the legacy auth process via direct messages
        to the user (using :func:`.BaseGateway.input` for instance)

        :param user_jid:
        :param registration_form:
        """
        raise NotImplementedError

    async def _on_user_unregister(self, iq: Iq):
        await self.unregister(user_store.get_by_stanza(iq), iq)

    async def unregister(self, user: GatewayUser, iq: Iq):
        """
        Called when the user unregister from the gateway

        :param user:
        :param iq:
        """
        raise NotImplementedError


log = logging.getLogger(__name__)
