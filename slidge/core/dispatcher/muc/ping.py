from typing import TYPE_CHECKING

from slixmpp import CoroutineCallback, Iq, StanzaPath
from slixmpp.exceptions import XMPPError

from ....group import LegacyMUC
from ..util import DispatcherMixin, exceptions_to_xmpp_errors

if TYPE_CHECKING:
    from slidge.core.gateway import BaseGateway


class PingMixin(DispatcherMixin):
    def __init__(self, xmpp: "BaseGateway"):
        super().__init__(xmpp)

        xmpp.remove_handler("Ping")
        xmpp.register_handler(
            CoroutineCallback(
                "Ping",
                StanzaPath("iq@type=get/ping"),
                self.__handle_ping,
            )
        )
        xmpp.plugin["xep_0030"].add_feature("urn:xmpp:ping")

    @exceptions_to_xmpp_errors
    async def __handle_ping(self, iq: Iq) -> None:
        ito = iq.get_to()
        if ito == self.xmpp.boundjid.bare:
            iq.reply().send()

        session = await self._get_session(iq)

        try:
            muc = await session.bookmarks.by_jid(ito)
        except XMPPError:
            pass
        else:
            self.__handle_muc_ping(muc, iq)
            return

        try:
            await session.contacts.by_jid(ito)
        except XMPPError:
            pass
        else:
            iq.reply().send()
            return

        raise XMPPError(
            "item-not-found", f"This JID does not match anything slidge knows: {ito}"
        )

    @staticmethod
    def __handle_muc_ping(muc: LegacyMUC, iq: Iq) -> None:
        if iq.get_from().resource in muc.get_user_resources():
            iq.reply().send()
        else:
            raise XMPPError("not-acceptable", etype="cancel", by=muc.jid)
