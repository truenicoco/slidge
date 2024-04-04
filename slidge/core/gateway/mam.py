from typing import TYPE_CHECKING

from slixmpp import CoroutineCallback, Iq, StanzaPath
from slixmpp.exceptions import XMPPError

if TYPE_CHECKING:
    from .base import BaseGateway


class Mam:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp
        xmpp.register_handler(
            CoroutineCallback(
                "MAM_query",
                StanzaPath("iq@type=set/mam"),
                self.__handle_mam,  # type:ignore
            )
        )
        xmpp.register_handler(
            CoroutineCallback(
                "MAM_get_from",
                StanzaPath("iq@type=get/mam"),
                self.__handle_mam_get_form,  # type:ignore
            )
        )
        xmpp.register_handler(
            CoroutineCallback(
                "MAM_get_meta",
                StanzaPath("iq@type=get/mam_metadata"),
                self.__handle_mam_metadata,  # type:ignore
            )
        )

    async def __handle_mam(self, iq: Iq):
        muc = await self.xmpp.get_muc_from_stanza(iq)
        await muc.send_mam(iq)

    async def __handle_mam_get_form(self, iq: Iq):
        ito = iq.get_to()

        if ito == self.xmpp.boundjid.bare:
            raise XMPPError(
                text="No MAM on the component itself, use a JID with a resource"
            )

        user = self.xmpp.store.users.get(iq.get_from())
        if user is None:
            raise XMPPError("registration-required")

        session = self.xmpp.get_session_from_user(user)

        await session.bookmarks.by_jid(ito)

        reply = iq.reply()
        form = self.xmpp.plugin["xep_0004"].make_form()
        form.add_field(ftype="hidden", var="FORM_TYPE", value="urn:xmpp:mam:2")
        form.add_field(ftype="jid-single", var="with")
        form.add_field(ftype="text-single", var="start")
        form.add_field(ftype="text-single", var="end")
        form.add_field(ftype="text-single", var="before-id")
        form.add_field(ftype="text-single", var="after-id")
        form.add_field(ftype="boolean", var="include-groupchat")
        field = form.add_field(ftype="list-multi", var="ids")
        field["validate"]["datatype"] = "xs:string"
        field["validate"]["open"] = True
        reply["mam"].append(form)
        reply.send()

    async def __handle_mam_metadata(self, iq: Iq):
        muc = await self.xmpp.get_muc_from_stanza(iq)
        await muc.send_mam_metadata(iq)
