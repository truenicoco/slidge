import logging
from typing import TYPE_CHECKING, Any, Optional

from slixmpp import JID
from slixmpp.exceptions import XMPPError
from slixmpp.types import OptJid

from ..util.db import user_store
from ..util.xep_0030.stanza.info import DiscoInfo

if TYPE_CHECKING:
    from ..core.gateway import BaseGateway


class Disco:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp

        xmpp.plugin["xep_0030"].set_node_handler(
            "get_info",
            jid=None,
            node=None,
            handler=self.get_info,
        )

    async def get_info(
        self, jid: OptJid, node: Optional[str], ifrom: OptJid, data: Any
    ):
        base = self.xmpp.plugin["xep_0030"].static.get_info(jid, node, ifrom, data)

        if ifrom == self.xmpp.boundjid.bare:
            return base

        if jid == self.xmpp.boundjid.bare:
            return base

        if ifrom is None:
            raise XMPPError("subscription-required")

        user = user_store.get_by_jid(ifrom)
        if user is None:
            raise XMPPError("registration-required")
        session = self.xmpp.get_session_from_user(user)  # type:ignore
        log.debug("Looking for entity: %s", jid)
        try:
            entity = await session.contacts.by_jid(jid)
        except XMPPError:
            entity = await session.bookmarks.by_jid(jid)
            if nick := JID(jid).resource:
                log.debug("Returning empty disco for participant")
                d = DiscoInfo()
                d.set_identities([("client", "pc", None, nick)])
                return d

        log.debug("entity: %s", entity)
        return entity.get_disco_info()


log = logging.getLogger(__name__)
