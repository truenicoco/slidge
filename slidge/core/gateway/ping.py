from typing import TYPE_CHECKING

from slixmpp import CoroutineCallback, Iq, StanzaPath
from slixmpp.exceptions import XMPPError

from ...group import LegacyMUC
from ...util.db import user_store

if TYPE_CHECKING:
    from .base import BaseGateway


class Ping:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp

        xmpp.remove_handler("Ping")
        xmpp.register_handler(
            CoroutineCallback(
                "Ping",
                StanzaPath("iq@type=get/ping"),
                self.__handle_ping,  # type:ignore
            )
        )
        xmpp.plugin["xep_0030"].add_feature("urn:xmpp:ping")

    async def __handle_ping(self, iq: Iq):
        ito = iq.get_to()

        if ito == self.xmpp.boundjid.bare:
            iq.reply().send()

        ifrom = iq.get_from()
        user = user_store.get_by_jid(ifrom)
        if user is None:
            raise XMPPError("registration-required")

        session = self.xmpp.get_session_from_user(user)
        session.raise_if_not_logged()

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
    def __handle_muc_ping(muc: LegacyMUC, iq: Iq):
        if iq.get_from().resource in muc.user_resources:
            iq.reply().send()
        else:
            raise XMPPError("not-acceptable", etype="cancel", by=muc.jid)
