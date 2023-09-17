from copy import copy
from typing import TYPE_CHECKING

from slixmpp import CoroutineCallback, Iq, StanzaPath
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0292.stanza import NS as VCard4NS

from ...contact import LegacyContact
from ...group import LegacyParticipant

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
        session = self.xmpp.get_session_from_stanza(iq)
        entity = await session.get_contact_or_group_or_participant(iq.get_to())
        if not entity:
            raise XMPPError("item-not-found")

        if isinstance(entity, LegacyParticipant):
            if not (contact := entity.contact):
                raise XMPPError("item-not-found", "This participant has no contact")
            vcard = await self.xmpp.vcard.get_vcard(contact.jid, iq.get_from())
            avatar = contact.get_avatar()
        else:
            avatar = entity.get_avatar()
            if isinstance(entity, LegacyContact):
                vcard = await self.xmpp.vcard.get_vcard(entity.jid, iq.get_from())
            else:
                vcard = None
        v = self.xmpp.plugin["xep_0054"].make_vcard()
        if avatar is not None and avatar.data:
            v["PHOTO"]["BINVAL"] = avatar.data.get_value()
            v["PHOTO"]["TYPE"] = "image/png"
        if vcard:
            for el in vcard.xml:
                new = copy(el)
                new.tag = el.tag.replace(f"{{{VCard4NS}}}", "")
                v.append(new)
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
