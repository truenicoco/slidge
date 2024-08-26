from slixmpp import CoroutineCallback, Iq, StanzaPath
from slixmpp.exceptions import XMPPError
from slixmpp.plugins.xep_0004 import Form
from slixmpp.xmlstream import StanzaBase

from ..util import DispatcherMixin, exceptions_to_xmpp_errors


class MucOwnerMixin(DispatcherMixin):
    def __init__(self, xmpp):
        super().__init__(xmpp)
        xmpp.register_handler(
            CoroutineCallback(
                "MUCOwnerGet",
                StanzaPath("iq@type=get/mucowner_query"),
                self.on_muc_owner_query,
            )
        )
        xmpp.register_handler(
            CoroutineCallback(
                "MUCOwnerSet",
                StanzaPath("iq@type=set/mucowner_query"),
                self.on_muc_owner_set,
            )
        )

    @exceptions_to_xmpp_errors
    async def on_muc_owner_query(self, iq: StanzaBase) -> None:
        assert isinstance(iq, Iq)
        muc = await self.get_muc_from_stanza(iq)

        reply = iq.reply()

        form = Form(title="Slidge room configuration")
        form["instructions"] = (
            "Complete this form to modify the configuration of your room."
        )
        form.add_field(
            var="FORM_TYPE",
            type="hidden",
            value="http://jabber.org/protocol/muc#roomconfig",
        )
        form.add_field(
            var="muc#roomconfig_roomname",
            label="Natural-Language Room Name",
            type="text-single",
            value=muc.name,
        )
        if muc.HAS_DESCRIPTION:
            form.add_field(
                var="muc#roomconfig_roomdesc",
                label="Short Description of Room",
                type="text-single",
                value=muc.description,
            )

        muc_owner = iq["mucowner_query"]
        muc_owner.append(form)
        reply.append(muc_owner)
        reply.send()

    @exceptions_to_xmpp_errors
    async def on_muc_owner_set(self, iq: StanzaBase) -> None:
        assert isinstance(iq, Iq)
        muc = await self.get_muc_from_stanza(iq)
        query = iq["mucowner_query"]

        if form := query.get_plugin("form", check=True):
            values = form.get_values()
            await muc.on_set_config(
                name=values.get("muc#roomconfig_roomname"),
                description=(
                    values.get("muc#roomconfig_roomdesc")
                    if muc.HAS_DESCRIPTION
                    else None
                ),
            )
            form["type"] = "result"
            clear = False
        elif destroy := query.get_plugin("destroy", check=True):
            reason = destroy["reason"] or None
            await muc.on_destroy_request(reason)
            user_participant = await muc.get_user_participant()
            user_participant._affiliation = "none"
            user_participant._role = "none"
            presence = user_participant._make_presence(ptype="unavailable", force=True)
            presence["muc"].enable("destroy")
            if reason is not None:
                presence["muc"]["destroy"]["reason"] = reason
            user_participant._send(presence)
            await muc.session.bookmarks.remove(muc, kick=False)
            clear = True
        else:
            raise XMPPError("bad-request")

        iq.reply(clear=clear).send()
