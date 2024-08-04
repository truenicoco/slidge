from slixmpp import JID, CoroutineCallback, Iq, StanzaPath
from slixmpp.exceptions import XMPPError
from slixmpp.xmlstream import StanzaBase

from ..util import DispatcherMixin, exceptions_to_xmpp_errors


class MucAdminMixin(DispatcherMixin):
    def __init__(self, xmpp) -> None:
        super().__init__(xmpp)
        self.xmpp.register_handler(
            CoroutineCallback(
                "MUCModerate",
                StanzaPath("iq/apply_to/moderate"),
                self.on_user_moderation,
            )
        )
        self.xmpp.register_handler(
            CoroutineCallback(
                "MUCSetAffiliation",
                StanzaPath("iq@type=set/mucadmin_query"),
                self.on_user_set_affiliation,
            )
        )
        self.xmpp.register_handler(
            CoroutineCallback(
                "MUCGetAffiliation",
                StanzaPath("iq@type=get/mucadmin_query"),
                self.on_muc_admin_query_get,
            )
        )

    @exceptions_to_xmpp_errors
    async def on_user_moderation(self, iq: StanzaBase) -> None:
        assert isinstance(iq, Iq)
        muc = await self.get_muc_from_stanza(iq)

        apply_to = iq["apply_to"]
        xmpp_id = apply_to["id"]
        if not xmpp_id:
            raise XMPPError("bad-request", "Missing moderated message ID")

        moderate = apply_to["moderate"]
        if not moderate["retract"]:
            raise XMPPError(
                "feature-not-implemented",
                "Slidge only implements moderation/retraction",
            )

        legacy_id = self._xmpp_msg_id_to_legacy(muc.session, xmpp_id)
        await muc.session.on_moderate(muc, legacy_id, moderate["reason"] or None)
        iq.reply(clear=True).send()

    @exceptions_to_xmpp_errors
    async def on_user_set_affiliation(self, iq: StanzaBase) -> None:
        assert isinstance(iq, Iq)
        muc = await self.get_muc_from_stanza(iq)

        item = iq["mucadmin_query"]["item"]
        if item["jid"]:
            contact = await muc.session.contacts.by_jid(JID(item["jid"]))
        else:
            part = await muc.get_participant(
                item["nick"], fill_first=True, raise_if_not_found=True
            )
            assert part.contact is not None
            contact = part.contact

        if item["affiliation"]:
            await muc.on_set_affiliation(
                contact,
                item["affiliation"],
                item["reason"] or None,
                item["nick"] or None,
            )
        elif item["role"] == "none":
            await muc.on_kick(contact, item["reason"] or None)

        iq.reply(clear=True).send()

    @exceptions_to_xmpp_errors
    async def on_muc_admin_query_get(self, iq: StanzaBase) -> None:
        assert isinstance(iq, Iq)
        affiliation = iq["mucadmin_query"]["item"]["affiliation"]

        if not affiliation:
            raise XMPPError("bad-request")

        session = await self._get_session(iq, 1, logged=True)
        muc = await session.bookmarks.by_jid(iq.get_to())

        reply = iq.reply()
        reply.enable("mucadmin_query")
        async for participant in muc.get_participants():
            if not participant.affiliation == affiliation:
                continue
            reply["mucadmin_query"].append(participant.mucadmin_item())
        reply.send()
