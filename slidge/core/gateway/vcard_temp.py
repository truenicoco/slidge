from typing import TYPE_CHECKING

from slixmpp import CoroutineCallback, Iq, StanzaPath
from slixmpp.exceptions import XMPPError

if TYPE_CHECKING:
    from .base import BaseGateway


class VCardTemp:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp
        xmpp.register_handler(
            CoroutineCallback(
                "VCardTemp",
                StanzaPath("iq/vcard_temp"),
                self.__handle_get_vcard_temp,  # type:ignore
            )
        )

    async def __handle_get_vcard_temp(self, iq: Iq):
        if iq["type"] != "get":
            raise XMPPError("not-authorized")

        muc = await self.xmpp.get_muc_from_stanza(iq)
        to = iq.get_to()

        if nick := to.resource:
            participant = await muc.get_participant(nick, raise_if_not_found=False)
            if not (contact := participant.contact):
                raise XMPPError("item-not-found", "This participant has no contact")
            avatar = contact.get_avatar()
            if avatar is None:
                raise XMPPError("item-not-found", "This participant has no avatar")
            data = avatar.data
            v = self.xmpp.plugin["xep_0054"].make_vcard()
            v["PHOTO"]["BINVAL"] = data.get_value()
            v["PHOTO"]["TYPE"] = "image/png"
            reply = iq.reply()
            reply.append(v)
            reply.send()
            return

        return await muc.send_avatar(iq)
