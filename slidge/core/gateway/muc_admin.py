from typing import TYPE_CHECKING

from slixmpp import CoroutineCallback, Iq, StanzaPath
from slixmpp.exceptions import XMPPError

if TYPE_CHECKING:
    from .base import BaseGateway


class MucAdmin:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp
        xmpp.register_handler(
            CoroutineCallback(
                "muc#admin",
                StanzaPath("iq@type=get/mucadmin_query"),
                self._handle_admin,  # type: ignore
            )
        )

    async def _handle_admin(self, iq: Iq):
        muc = await self.xmpp.get_muc_from_stanza(iq)

        affiliation = iq["mucadmin_query"]["item"]["affiliation"]

        if not affiliation:
            raise XMPPError("bad-request")

        reply = iq.reply()
        reply.enable("mucadmin_query")
        async for participant in muc.get_participants():
            if not participant.affiliation == affiliation:
                continue
            reply["mucadmin_query"].append(participant.mucadmin_item())
        reply.send()
