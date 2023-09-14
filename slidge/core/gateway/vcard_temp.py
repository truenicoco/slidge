from typing import TYPE_CHECKING

from slixmpp import CoroutineCallback, Iq, StanzaPath
from slixmpp.exceptions import XMPPError

if TYPE_CHECKING:
    from .base import BaseGateway


class VCardTemp:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp
        # remove slixmpp's default handler to replace with our own
        self.xmpp.remove_handler("VCardTemp")
        xmpp.register_handler(
            CoroutineCallback(
                "VCardTemp",
                StanzaPath("iq/vcard_temp"),
                self.__handler,  # type:ignore
            )
        )

    async def __handler(self, iq: Iq):
        if iq["type"] == "get":
            return await self.__handle_get_vcard_temp(iq)

        if iq["type"] == "set":
            return await self.__handle_set_vcard_temp(iq)

    async def __handle_get_vcard_temp(self, iq: Iq):
        muc = await self.xmpp.get_muc_from_stanza(iq)
        to = iq.get_to()

        if nick := to.resource:
            participant = await muc.get_participant(nick, raise_if_not_found=True)
            if not (contact := participant.contact):
                raise XMPPError("item-not-found", "This participant has no contact")
            avatar = contact.get_avatar()
        else:
            avatar = muc.get_avatar()
        if avatar is None:
            raise XMPPError("item-not-found")
        data = avatar.data
        v = self.xmpp.plugin["xep_0054"].make_vcard()
        v["PHOTO"]["BINVAL"] = data.get_value()
        v["PHOTO"]["TYPE"] = "image/png"
        reply = iq.reply()
        reply.append(v)
        reply.send()

    async def __handle_set_vcard_temp(self, iq: Iq):
        muc = await self.xmpp.get_muc_from_stanza(iq)
        to = iq.get_to()

        if to.resource:
            raise XMPPError("bad-request", "You cannot set participants avatars")

        data = iq["vcard_temp"]["PHOTO"]["BINVAL"] or None
        try:
            legacy_id = await muc.admin_set_avatar(
                data, iq["vcard_temp"]["PHOTO"]["TYPE"] or None
            )
        except XMPPError:
            raise
        except Exception as e:
            raise XMPPError("internal-server-error", str(e))
        reply = iq.reply(clear=True)
        reply.enable("vcard_temp")
        reply.send()

        if not data:
            await muc.set_avatar(None, blocking=True)
            return

        if legacy_id:
            await muc.set_avatar(data, legacy_id, blocking=True)
