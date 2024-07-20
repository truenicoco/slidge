from copy import copy
from typing import TYPE_CHECKING

from slixmpp import CoroutineCallback, Iq, StanzaPath, register_stanza_plugin
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0084 import MetaData
from slixmpp.plugins.xep_0292.stanza import NS as VCard4NS

from ...contact import LegacyContact
from ...core.session import BaseSession
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
        # TODO: MR to slixmpp adding this to XEP-0084
        register_stanza_plugin(
            self.xmpp.plugin["xep_0060"].stanza.Item,
            MetaData,
        )

    async def __handler(self, iq: Iq):
        if iq["type"] == "get":
            return await self.__handle_get_vcard_temp(iq)

        if iq["type"] == "set":
            return await self.__handle_set_vcard_temp(iq)

    async def __fetch_user_avatar(self, session: BaseSession):
        hash_ = session.user.avatar_hash
        if not hash_:
            raise XMPPError(
                "item-not-found", "The slidge user does not have any avatar set"
            )
        meta_iq = await self.xmpp.plugin["xep_0060"].get_item(
            session.user_jid,
            MetaData.namespace,
            hash_,
            ifrom=self.xmpp.boundjid.bare,
        )
        info = meta_iq["pubsub"]["items"]["item"]["avatar_metadata"]["info"]
        type_ = info["type"]
        data_iq = await self.xmpp.plugin["xep_0084"].retrieve_avatar(
            session.user_jid, hash_, ifrom=self.xmpp.boundjid.bare
        )
        bytes_ = data_iq["pubsub"]["items"]["item"]["avatar_data"]["value"]
        return bytes_, type_

    async def __handle_get_vcard_temp(self, iq: Iq):
        session = self.xmpp.get_session_from_stanza(iq)
        entity = await session.get_contact_or_group_or_participant(iq.get_to(), False)
        if not entity:
            raise XMPPError("item-not-found")

        bytes_ = None
        if isinstance(entity, LegacyParticipant):
            if entity.is_user:
                bytes_, type_ = await self.__fetch_user_avatar(session)
                if not bytes_:
                    raise XMPPError(
                        "internal-server-error",
                        "Could not fetch the slidge user's avatar",
                    )
                avatar = None
                vcard = None
            elif not (contact := entity.contact):
                raise XMPPError("item-not-found", "This participant has no contact")
            else:
                vcard = await self.xmpp.vcard.get_vcard(contact.jid, iq.get_from())
                avatar = contact.get_avatar()
                type_ = "image/png"
        else:
            avatar = entity.get_avatar()
            type_ = "image/png"
            if isinstance(entity, LegacyContact):
                vcard = await self.xmpp.vcard.get_vcard(entity.jid, iq.get_from())
            else:
                vcard = None
        v = self.xmpp.plugin["xep_0054"].make_vcard()
        if avatar is not None and avatar.data:
            bytes_ = avatar.data.get_value()
        if bytes_:
            v["PHOTO"]["BINVAL"] = bytes_
            v["PHOTO"]["TYPE"] = type_
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
            legacy_id = await muc.on_avatar(
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
