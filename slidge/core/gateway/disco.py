import logging
from typing import TYPE_CHECKING, Any, Optional

from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0030.stanza.items import DiscoItems
from slixmpp.types import OptJid

if TYPE_CHECKING:
    from .base import BaseGateway


class Disco:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp

        xmpp.plugin["xep_0030"].set_node_handler(
            "get_info",
            jid=None,
            node=None,
            handler=self.get_info,
        )

        xmpp.plugin["xep_0030"].set_node_handler(
            "get_items",
            jid=None,
            node=None,
            handler=self.get_items,
        )

    async def get_info(
        self, jid: OptJid, node: Optional[str], ifrom: OptJid, data: Any
    ):
        if ifrom == self.xmpp.boundjid.bare or jid in (self.xmpp.boundjid.bare, None):
            return self.xmpp.plugin["xep_0030"].static.get_info(jid, node, ifrom, data)

        if ifrom is None:
            raise XMPPError("subscription-required")

        user = self.xmpp.store.users.get(ifrom)
        if user is None:
            raise XMPPError("registration-required")
        session = self.xmpp.get_session_from_user(user)
        await session.wait_for_ready()

        log.debug("Looking for entity: %s", jid)

        assert jid is not None
        entity = await session.get_contact_or_group_or_participant(jid)

        if entity is None:
            raise XMPPError("item-not-found")

        return await entity.get_disco_info(jid, node)

    async def get_items(
        self, jid: OptJid, node: Optional[str], ifrom: OptJid, data: Any
    ):
        if ifrom is None:
            raise XMPPError("bad-request")

        if jid != self.xmpp.boundjid.bare:
            return DiscoItems()

        user = self.xmpp.store.users.get(ifrom)
        if user is None:
            raise XMPPError("registration-required")

        session = self.xmpp.get_session_from_user(user)
        await session.wait_for_ready()

        d = DiscoItems()
        for muc in sorted(session.bookmarks, key=lambda m: m.name):
            d.add_item(muc.jid, name=muc.name)

        return d


log = logging.getLogger(__name__)
