import logging
from typing import TYPE_CHECKING

from slixmpp import Presence
from slixmpp.exceptions import XMPPError
from slixmpp.xmlstream import StanzaBase

from .util import DispatcherMixin

if TYPE_CHECKING:
    from slidge.core.gateway import BaseGateway


class CapsMixin(DispatcherMixin):
    def __init__(self, xmpp: "BaseGateway"):
        super().__init__(xmpp)
        xmpp.del_filter("out", xmpp.plugin["xep_0115"]._filter_add_caps)
        xmpp.add_filter("out", self._filter_add_caps)  # type:ignore

    async def _filter_add_caps(self, stanza: StanzaBase) -> StanzaBase:
        # we rolled our own "add caps on presences" filter because
        # there is too much magic happening in slixmpp
        # anyway, we probably want to roll our own "dynamic disco"/caps
        # module in the long run, so it's a step in this direction
        if not isinstance(stanza, Presence):
            return stanza

        if stanza.get_plugin("caps", check=True):
            return stanza

        if stanza["type"] not in ("available", "chat", "away", "dnd", "xa"):
            return stanza

        pfrom = stanza.get_from()

        caps = self.xmpp.plugin["xep_0115"]

        if pfrom != self.xmpp.boundjid.bare:
            try:
                session = self.xmpp.get_session_from_jid(stanza.get_to())
            except XMPPError:
                log.debug("not adding caps 1")
                return stanza

            if session is None:
                return stanza

            await session.ready

            try:
                contact = await session.contacts.by_jid(pfrom)
            except XMPPError:
                return stanza
            ver = await contact.get_caps_ver(pfrom)
        else:
            ver = await caps.get_verstring(pfrom)

        log.debug("Ver: %s", ver)

        if ver:
            stanza["caps"]["node"] = caps.caps_node
            stanza["caps"]["hash"] = caps.hash
            stanza["caps"]["ver"] = ver
        return stanza


log = logging.getLogger(__name__)
